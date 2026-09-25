#!/usr/bin/env python3
"""recipes -- §7's three finite loops, and the record of every run.

WHAT THIS IS
------------
§7 names three loops -- Perception, Work, Report -- each with a trigger, an
action and a condition under which it stops normally. It also states four
properties they must have: no change produces no model work, a restart does not
dispatch one piece of work twice, a loop stops when its budget is spent or when
there is no progress, and a pending decision blocks its dependents and nothing
else.

None of those four is a thing a loop can be said to have. They are properties of
the RECORD the loop leaves, which is why this module is mostly about the record:

  * the key is `recipe + scope + period`, and it is the path the record is
    written to. Two runs of the same key are the same run, and the second one
    finds the first one's file. "Restart does not dispatch twice" is not a check
    inside the loop; it is that the file is already there.
  * the record is written twice: CLAIMED before the first step, and the outcome
    at the end. A run that dies in between leaves a claim and no outcome, and
    the next run of that key says so. This is the difference between "nobody has
    done this period" and "somebody started it and nobody knows how far they
    got", and the two must not render alike.
  * `no change` is decided before the model boundary is reached, so a quiet
    period does not consult it. Budget is counted at the boundary, and the
    boundary refuses -- loudly, with the step it would have taken -- when no
    transport is bound.

WHAT IT REUSES, AND WHY NOTHING IS REIMPLEMENTED HERE
-----------------------------------------------------
Every measurement and every write in this module belongs to a module that
already had it:

  `intake/workchain.py`    the authored list of what a project follows
                           (`load_sources`), the authored selection
                           (`load_today`), and the reasons those can be absent
                           as distinct from empty.
  `runtime/daily_report.py` the worktree delta, the Quay ledger, the
                           three-valued progress verdict, the composition, the
                           render, the receipt and the publish.
  `runtime/agent_control_store.py`
                           the one exclusive lock in this tree (`_exclusive`)
                           and the one atomic JSON write (`_atomic_write`).
                           Both are reached the way `runtime/daily_report.py`
                           already reaches them, for the reason written there:
                           a second implementation of either is a second place
                           for the same bug to live.

The consequence worth naming: this file opens no lock, replaces no file and runs
no command itself. It has no actuator call sites of its own, which is why
`runtime/actuator-contract.yaml` gained nothing for it.

WHAT IT DOES NOT DO
-------------------
It does not call a model. `runtime/recipe-contract.yaml#model.transports` is
empty, and the engine treats a binding that names one of nothing as
MODEL_UNBOUND. A step that needs a model is recorded with what it would have
been asked, and nothing is sent. §B does not authorize a new paid service, and a
loop that read an unbound transport as "nothing to do" would report a clean
period it never examined.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - the tree's environment guarantee
    yaml = None

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
CONTRACT = _HERE / "recipe-contract.yaml"

SCHEMA_CONTRACT = "serveros/recipe-contract.v1"
SCHEMA_RECORD = "serveros/recipe-record.v1"

#: The namespace this module writes into, and the ref that resolves to it.
#: Both are read by `state_root_for`; neither is composed anywhere else.
NAMESPACE = "recipes"
NAMESPACE_REF = f"state/{NAMESPACE}"

#: §7: what a schedule must bind before a loop may be enabled under it. A
#: function rather than a constant because it is read from the contract, and a
#: module-level read would make importing this module depend on the contract
#: parsing -- an import that can fail is worse than a call that can refuse.
def required_schedule_fields() -> tuple:
    return tuple(contract()["schedules"]["required_fields"])

#: The three exit codes, and the rule that separates them. 0 is "the loop ran
#: and stopped for a reason it declared in advance", including the reasons that
#: mean it did nothing. 2 is "the loop could not tell" -- an unbound transport,
#: a list nobody authored, a claim with no outcome. 1 is a fault. A cron log
#: that renders the middle one as the first destroys the only signal an
#: unattended loop has.
EXIT_OK = 0
EXIT_FAULT = 1
EXIT_COULD_NOT_ANSWER = 2

CLAIMED = "CLAIMED"
DONE = "DONE"
STOPPED = "STOPPED"

NEVER_RUN = "NEVER_RUN"

#: The step that reads a scope's sources. Named once because both the reading
#: and the refusal to read have to agree on it.
_SOURCE_READERS = {"node": "declared", "project": "workchain", "item": "declared"}


class Refused(ValueError):
    """The caller asked for something the contract does not have."""


class Fault(RuntimeError):
    """A step raised. Distinct from a stop: a stop is declared in advance."""


class Stop(Exception):
    """A loop's own reason for stopping. Not an error -- a declared outcome."""

    def __init__(self, reason: str, why: str, **outcomes) -> None:
        super().__init__(f"{reason}: {why}")
        self.reason = reason
        self.why = why
        self.outcomes = outcomes


class Unbound(Exception):
    """A step needed something this node has not bound, or has not authored."""

    def __init__(self, reason: str, why: str, **outcomes) -> None:
        super().__init__(f"{reason}: {why}")
        self.reason = reason
        self.why = why
        self.outcomes = outcomes


# ---------------------------------------------------------------------------
# Loading the modules this one reuses
# ---------------------------------------------------------------------------

def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _daily():
    return _load("serveros_runtime_daily_report", _HERE / "daily_report.py")


def _store():
    return _load("serveros_runtime_agent_control_store",
                 _HERE / "agent_control_store.py")


def _workchain():
    return _load("serveros_intake_workchain", ROOT / "intake" / "workchain.py")


def _paths():
    return _load("serveros_runtime_paths", _HERE / "paths.py")


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------

def contract(path: Path | str = CONTRACT) -> dict:
    if yaml is None:  # pragma: no cover - environment guarantee
        raise Refused("PyYAML is not importable, so the recipe contract cannot be read")
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise Refused(f"{path} did not parse into a mapping")
    if document.get("schema") != SCHEMA_CONTRACT:
        raise Refused(f"{path} declares schema {document.get('schema')!r}, "
                      f"not {SCHEMA_CONTRACT!r}")
    return document


def recipes() -> tuple:
    return tuple(contract()["recipes"] or ())


def recipe(recipe_id: str) -> dict:
    for entry in recipes():
        if entry["id"] == recipe_id:
            return entry
    raise Refused(
        f"{recipe_id!r} is not one of §7's loops ({[r['id'] for r in recipes()]}). "
        f"A fourth loop is a decision, and it is not one this contract records.")


def stop_reasons() -> dict:
    return contract()["stops"]


def stops_from_the_contract() -> tuple:
    return tuple(stop_reasons())


def model_steps(recipe_id: str) -> tuple:
    return tuple(step["id"] for step in recipe(recipe_id)["steps"]
                 if step.get("needs_model"))


def scopes() -> tuple:
    return tuple(contract()["scopes"] or ())


def model_transports() -> tuple:
    return tuple(contract()["model"].get("transports") or ())


def exit_code_for(reason: str) -> int:
    """0 if the loop stopped for a reason it declared; 2 if it could not tell."""
    entry = stop_reasons().get(reason)
    if entry is None:
        raise Refused(f"{reason!r} is not in the contract's stop vocabulary; "
                      f"the engine produced a word nobody can read")
    return EXIT_OK if entry.get("normal") else EXIT_COULD_NOT_ANSWER


# ---------------------------------------------------------------------------
# Periods, scopes, keys
# ---------------------------------------------------------------------------

def _parse_instant(text) -> datetime:
    """A timestamp with an offset, or a refusal.

    No naive default. `daily_report._parse_instant` states the reason for its
    own window and it holds here unchanged: a reading that does not say which
    clock it is on gets moved by hours, and stays a well-formed timestamp while
    it does.
    """
    if not isinstance(text, str) or not text.strip():
        raise Refused(f"{text!r} is not an ISO-8601 instant")
    stamp = text.strip()
    if stamp.endswith("Z"):
        stamp = stamp[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError as exc:
        raise Refused(f"{text!r} is not an ISO-8601 instant ({exc})") from exc
    if parsed.tzinfo is None:
        raise Refused(
            f"{text!r} carries no offset. A period is compared against commit "
            f"times and gate records, so an instant that does not say which "
            f"clock it is on is a period shifted by an unknown number of hours")
    return parsed


def parse_period(*, start, end) -> dict:
    first, last = _parse_instant(start), _parse_instant(end)
    if first > last:
        raise Refused(f"the period starts ({start}) after it ends ({end})")
    return {"start": first.isoformat(), "end": last.isoformat(),
            "id": f"{first:%Y%m%dT%H%M%z}-{last:%Y%m%dT%H%M%z}"}


def parse_scope(text: str) -> dict:
    kind, _, name = str(text).partition(":")
    known = {entry["id"]: entry for entry in scopes()}
    if kind not in known:
        raise Refused(
            f"{text!r} names scope {kind!r}; the contract declares "
            f"{sorted(known)}. A scope this contract cannot read is one whose "
            f"inputs nobody declared, and guessing them is how a loop reports "
            f"a quiet period it never looked at")
    if kind in ("project", "item") and not name:
        raise Refused(f"{text!r} names no {kind}. `{kind}:` with nothing after "
                      f"the colon is a scope that ranges over everything")
    return {"ref": text, "kind": kind, "name": name or None,
            "reads": known[kind]["reads"],
            "unproduced_means": known[kind]["unproduced_means"]}


def scope_slug(scope_ref: str) -> str:
    return str(scope_ref).replace(":", "-").replace("/", "_")


def record_path(root, *, recipe_id: str, scope_ref: str, period_id: str) -> Path:
    return Path(root) / recipe_id / scope_slug(scope_ref) / f"{period_id}.json"


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

def last_success(state_root, *, recipe_id: str, scope_ref: str) -> dict | None:
    """This scope's most recent DONE record, whatever period it was for.

    §7: "持久 last-success/cursor". The cursor is what makes "nothing has moved
    since we last looked" answerable, and "we last looked" is a fact about the
    scope rather than about one period of it. A cursor kept only inside a
    period's own record is not persistent: the next period has no previous
    record for its key, so it would treat every source as first-seen and
    consult the model, which is the exact cost the increment gate exists to
    avoid.

    Selection is by `finished_at`, and a record with no cursor does not win --
    a run that stopped before it read anything has nothing to hand forward.

    Two records can carry the same `finished_at`: the clock is injectable, and
    the default one has second resolution. The tie is broken by the record's
    FILE NAME, which carries the period, and one period's name sorts after an
    earlier period's -- comparing only the timestamp left the earlier record
    winning, which is the one direction a cursor must never move in.
    """
    directory = Path(state_root) / recipe_id / scope_slug(scope_ref)
    if not directory.is_dir():
        return None
    best = None
    for candidate in sorted(directory.glob("*.json")):
        document = read_record(candidate)
        if not document or document.get("state") != DONE:
            continue
        if not document.get("cursor"):
            continue
        order = (str(document.get("finished_at") or ""), candidate.name)
        if best is None or order > best[0]:
            best = (order, document)
    return best[1] if best else None


def read_record(path) -> dict | None:
    path = Path(path)
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A record that does not parse is not "never run". It is a record
        # somebody must look at, and answering NEVER_RUN here would erase it.
        return {"schema": SCHEMA_RECORD, "state": "UNREADABLE",
                "path": str(path), "unreadable": True}
    document["path"] = str(path)
    return document


def _write_record(path: Path, record: dict) -> dict:
    """Write under the same lock every other writer of this tree takes.

    The lock is held briefly -- for this write alone -- and that is enough,
    because the CLAIM is the exclusion: a second run of the same key takes the
    lock, re-reads, and finds the claim rather than an empty directory. Holding
    the lock across the whole run would add nothing except a way for one hung
    step to block the period forever.

    Both primitives belong to `runtime/agent_control_store.py`, and they are
    reached the way `runtime/daily_report.py` already reaches them. A second
    exclusive-lock implementation is a second place for the same race to live,
    and the sidecar rule (`<record>.lock`, never unlinked) is the part that a
    reimplementation is most likely to get subtly wrong.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(record)
    payload.pop("path", None)
    with _store()._exclusive(_lock_path(path)):
        _store()._atomic_write(path, payload)
    return payload


def _lock_path(path: Path) -> Path:
    return Path(str(path) + ".lock")


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def _quay_block(ctx: Context) -> dict:
    """Quay's ledger for this window, or the honest shape of not reading it.

    The absent shape is `runtime/cli.py#cmd_report_daily`'s, field for field.
    It is deliberately not `{"gates": []}`: an empty gate list is a measurement
    that came back zero, and this is a source nobody read. `progress_verdict`
    reads `answered` and would call the second one "no progress" if the field
    were missing.
    """
    if ctx.run_dir:
        return ctx.daily.measure_quay(Path(ctx.run_dir),
                                      ctx.daily.period_window(ctx.period),
                                      ctx.daily.Fallbacks())
    return {"root": None, "ledger": None, "tasks_dir": None, "answered": False,
            "gates": [], "tasks": [], "problems": [], "notes": [],
            "out_of_window": 0, "unknown_verdicts": [],
            "why": ("没有给 --run-dir，这一次没有读 Quay 的账本。"
                    "这是缺口，不是「本周期没有闸门记录」")}


def _head(path) -> tuple[str | None, str]:
    """One checkout's HEAD now, read-only, through the daily report's `git`.

    A checkout that did not answer returns None WITH its reason -- never a
    placeholder revision. `unknown` as a value would compare equal to itself
    next period and read as "nothing moved", which is the quiet kind of wrong:
    a source that has been unreachable for a month would look stable.
    """
    code, out = _daily()._git(Path(str(path)), "rev-parse", "HEAD")
    if code != 0:
        return None, (f"`git rev-parse HEAD` in {path} exited {code}: "
                      f"{out.strip()}")
    return out.strip(), ""


def _revision_of(entry: dict) -> tuple[str | None, str]:
    """One source's revision now, read-only."""
    kind = str(entry.get("kind") or "")
    if kind != "git":
        return None, (f"source {entry.get('id')!r} declares kind {kind!r}, and "
                      f"the contract's only observable kind today is 'git'")
    target = entry.get("path")
    if not target:
        return None, f"source {entry.get('id')!r} declares no path"
    revision, why = _head(target)
    if revision is None:
        return None, f"source {entry.get('id')!r}: {why}"
    return revision, ""


def checkout_revision(path) -> tuple[str | None, str]:
    """The revision a run's worktree is at, or None with its reason.

    This is the `revision` field of the record: which bytes the run's reading
    of "this period's work" was taken against. A path that is not a checkout
    gets None and the reason, because a record naming a revision nobody
    observed is worse than one naming none.
    """
    if not path:
        return None, "no worktree was given, so this record names no revision"
    return _head(path)


def read_sources(ctx) -> dict:
    """What this scope follows, from wherever the contract says it lives.

    The three states the reader can be in are kept apart all the way out,
    because `workchain.load_sources` already keeps them apart and flattening
    them here would undo that: a scope with no list is not a scope that follows
    nothing.
    """
    if ctx.scope["kind"] == "project":
        chain = _workchain()
        return chain.load_sources(records_root=ctx.records_root,
                                  project=ctx.scope["name"])
    if ctx.sources is not None:
        return {"state": "READ", "sources": list(ctx.sources),
                "path": str(ctx.sources_path or "<given>"), "why": ""}
    return {"state": "UNPRODUCED", "sources": [], "path": None,
            "why": (f"scope {ctx.scope['ref']!r} has no authored list of what it "
                    f"follows. This is a decision a person makes and not a "
                    f"measurement this loop can take: the same reading "
                    f"`workchain.load_sources` gives an absent sources.yaml, "
                    f"carried out to the caller")}


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

class Context:
    """What a loop is handed: the key, the inputs, and the two boundaries.

    A loop function does three things with this: it records a step, it spends at
    the model boundary, or it stops. It cannot reach the filesystem through it
    except through the modules named in `_daily`, `_workchain` and `_store`, and
    it cannot construct a record -- the engine owns the record's shape, so that
    the two runs of a key cannot disagree about it.
    """

    def __init__(self, *, engine, running: dict) -> None:
        self._engine = engine
        self.running = running
        self.recipe = running["recipe"]
        self.scope = engine.scope
        self.period = engine.period
        self.budget = running["budget"]["given"]
        self.state_root = engine.state_root
        self.records_root = engine.records_root
        self.workspace_root = engine.workspace_root
        self.run_dir = engine.run_dir
        self.worktree = engine.worktree
        self.out_dir = engine.out_dir
        self.recipient = engine.recipient
        self.channel = engine.channel
        self.publication_state = engine.publication_state
        self.sources = engine.sources
        self.sources_path = engine.sources_path
        self.work_engine = engine.work_engine
        self.now = engine.now
        self.daily = _daily()

    def step(self, step_id: str, did: str, **detail) -> None:
        entry = {"step": step_id, "did": did, "at": self.now()}
        if detail:
            entry["detail"] = detail
        self.running["steps"].append(entry)

    def note(self, text: str) -> None:
        self.running["notes"].append(text)

    def refresh_cursor(self, cursor: dict) -> None:
        self.running["cursor"] = dict(cursor)

    def stop(self, reason: str, why: str, **outcomes):
        raise Stop(reason, why, **outcomes)

    def unbound(self, reason: str, why: str, **outcomes):
        raise Unbound(reason, why, **outcomes)

    def spend(self, step_id: str, *, ask: dict):
        """The model boundary. Both refusals happen before anything is sent.

        The order matters and is the criterion: budget first, then transport.
        A run with no budget left does not need to know whether a transport is
        bound, and a run that reports MODEL_UNBOUND when its ceiling was already
        spent would be hiding the cheaper fact behind the more interesting one.
        """
        spent = self.running["model_calls"]
        if spent >= self.budget:
            self.running["would_call"] = {"step": step_id, "ask": ask,
                                          "why": "budget exhausted"}
            self.stop(
                "BUDGET_EXHAUSTED",
                f"step {step_id!r} needs a model and this run's ceiling is "
                f"{self.budget}, of which {spent} is spent. The ceiling is "
                f"given rather than defaulted: a budget a loop picked for "
                f"itself is one nobody agreed to.")
        transport = self._engine.transport
        if transport is None:
            self.running["would_call"] = {
                "step": step_id, "ask": ask,
                "why": ("no model transport is bound on this node. "
                        f"{CONTRACT.name}#model.transports is empty and "
                        "state/serveros/recipes/model-binding.yaml, when it "
                        "exists, may only name a transport from that list")}
            self.unbound(
                "MODEL_UNBOUND",
                f"step {step_id!r} is warranted and no model transport is "
                f"bound, so nothing was sent. The step is recorded with what it "
                f"would have been asked; treating this as 'nothing to do' would "
                f"report a quiet period this run never examined.")
        self.running["model_calls"] = spent + 1
        self.step(step_id, f"called the bound model transport {transport!r}",
                  ask=ask)
        return self._engine.model(transport, ask, self)


# ---------------------------------------------------------------------------
# §7 Perception -- 增量感知
# ---------------------------------------------------------------------------

def perception(ctx: Context) -> None:
    """What has moved in what this scope follows, since we last looked.

    Three facts are held apart here, and the separations are the point:

      * **what was read, per source.** The capture is recorded as its own step
        and the cursor is advanced one source at a time. A source that did not
        answer is not written off as unchanged -- and, the other half of the
        same rule, it does not take the sources that DID answer down with it. A
        pass where one checkout is unreachable still reads the rest and still
        reports their delta.
      * **a source's delta, and what the delta means.** The comparison is one
        step; the reading a moved revision warrants is another, with its own
        cost and its own refusal.
      * **a moved revision, and a new Item.** They are not the same fact. This
        loop says what understanding a delta touches. Opening work is a decision
        made from an accepted goal, and nothing here makes it.
    """
    sources = read_sources(ctx)
    if sources["state"] != "READ":
        ctx.step("sources", f"the list of what to follow is {sources['state']}",
                 path=sources.get("path"))
        # UNPRODUCED and UNREADABLE are different findings and get different
        # words: nobody wrote the list, versus the list is there and did not
        # parse. `workchain.load_sources` already made that distinction; this
        # carries it out rather than flattening it at the boundary.
        reason = ("SOURCES_UNPRODUCED" if sources["state"] == "UNPRODUCED"
                  else "SOURCE_UNAVAILABLE")
        ctx.unbound(reason, sources["why"])
    entries = sources["sources"]
    ctx.step("sources", f"{len(entries)} source(s) followed",
             path=sources.get("path"))

    observed, unanswered = {}, []
    for entry in entries:
        revision, why = _revision_of(entry)
        if revision is None:
            unanswered.append({"id": entry.get("id"), "why": why})
        else:
            observed[str(entry.get("id"))] = revision

    # The capture, recorded before anything is made of it: one entry per source
    # that answered, and the ones that did not, named with their own reasons.
    # This is the step `recipe-contract.yaml` declares for this loop and the
    # loop did not take; what the gap cost is not cosmetic -- `work waterline`
    # answers "how far did the capture get, and how far did understanding get"
    # per source, and a record with no capture step has nothing to put on the
    # first half of that comparison.
    ctx.step("observe",
             f"{len(observed)} of {len(entries)} source(s) answered",
             captured=sorted(observed),
             unanswered=[u["id"] for u in unanswered])
    if unanswered:
        # Written here rather than only on the refusal below: a pass that could
        # not read one source and then finished normally still has to say so, or
        # the record reads as a complete reading of a list it only partly read.
        ctx.running["outcomes"]["unread"] = unanswered

    if not observed:
        # Nothing at all was read, so there is no delta to report and no cursor
        # to move. This is the one case where the whole pass is unanswerable.
        ctx.unbound(
            "SOURCE_UNAVAILABLE",
            f"none of {len(entries)} source(s) answered: "
            f"{[u['id'] for u in unanswered]}. The cursor is left where it was, "
            f"so nothing this pass could not read is written off as unchanged.",
            unanswered=unanswered)

    cursor = dict(ctx.running.get("cursor") or {})
    moved = {name: revision for name, revision in observed.items()
             if cursor.get(name) != revision}
    fresh = [name for name in moved if name not in cursor]
    ctx.step("delta", f"{len(moved)} of {len(observed)} source(s) moved"
                      + (f", {len(fresh)} seen for the first time" if fresh else ""),
             moved=sorted(moved), first_seen=sorted(fresh))

    if not moved and unanswered:
        # "No change" is a statement about every source this scope follows, and
        # a source that did not answer cannot be part of it. Stopping NO_CHANGE
        # here would be the same write-off the refusal above exists to avoid,
        # only quieter: the quiet period would be reported for a source the pass
        # never looked at.
        ctx.unbound(
            "SOURCE_UNAVAILABLE",
            f"{len(unanswered)} of {len(entries)} source(s) did not answer and "
            f"the {len(observed)} that did are unchanged: "
            f"{[u['id'] for u in unanswered]}. Nothing moved that this pass "
            f"could see, and that is not the same as nothing moved.",
            unanswered=unanswered)

    if not moved:
        # The criterion, spent in one line: no change, so the model step below
        # is not reached and no model is called. The cursor is not rewritten --
        # it already says exactly this.
        ctx.stop("NO_CHANGE",
                 "every source this scope follows has the same revision as it "
                 "had at this scope's last success")

    # Something moved, and saying what a source delta means for this scope's
    # understanding is the step that needs a model. It is downstream of the
    # delta on purpose: that ordering is what makes "no change produces no LLM
    # work" a property of the code rather than a promise about it.
    reply = ctx.spend("refresh", ask={
        "reason": "say what a source delta means for this scope's understanding",
        "scope": ctx.scope["ref"],
        "period": ctx.period["id"],
        "moved": {name: {"was": cursor.get(name), "now": revision}
                  for name, revision in sorted(moved.items())},
        # What this asks for, and what it refuses to ask for. A moved revision
        # is a fact about a source. "A new Item" is a decision made from an
        # accepted goal by the work engine, and asking a reading to propose one
        # is the shortcut §P3 names: 「source变了」read directly as 「新Item候选」.
        # The views are not enumerated here on purpose -- the four Current Views
        # are directory scopes of the understanding layer, no module holds their
        # names as a constant, and a list restated in this ask would be a second
        # authority for a set the writer already knows.
        "instruction": (
            "For each source whose revision moved, say what the change may mean "
            "for this scope's current understanding and which part of it the "
            "change touches, so a reading can be pinned to the right range. Do "
            "not propose Items and do not say what work to open: a revision "
            "moving is not by itself a candidate for a new Item, and opening "
            "work is decided from an accepted goal rather than from a delta. "
            "Say nothing about sources that did not move."),
    })
    ctx.running["outcomes"]["refresh"] = reply
    # The cursor advances only here -- past every refusal above. A run that
    # stopped could not read one of its sources, and moving the cursor then
    # would lose that source's next change: the revision it would be compared
    # against would already be the newer one. It advances PER SOURCE: what was
    # read is written down, and a source that did not answer keeps the revision
    # we last actually read instead of being dropped out of the map. When every
    # source answered there is nothing to carry and the cursor is exactly what
    # was read -- so a source taken off the followed list does not keep an entry
    # for a revision nobody follows any more, past the next pass that advances
    # the cursor at all. (A quiet pass does not advance it; there is nothing to
    # say and the record already says it.)
    ctx.refresh_cursor({**cursor, **observed} if unanswered else observed)


# ---------------------------------------------------------------------------
# §7 Work -- 执行
# ---------------------------------------------------------------------------

def _read_entry(ctx, item_id: str) -> dict:
    """One queue entry's own record: its state, its decision, its edges.

    Read from the Item's own directory rather than from a projection of it.
    `intake/board-contract.yaml` already names the Item as "the only authority
    on its own state", and a board row is a projection that may not exist yet.
    """
    directory = ctx.records_root / "projects" / ctx.scope["name"] / "items" / item_id
    card = directory / "card.yaml"
    if not card.is_file():
        return {"id": item_id, "state": "UNREADABLE", "awaiting": None,
                "depends_on": None, "path": str(card),
                "why": f"no card at {card}"}
    try:
        document = yaml.safe_load(card.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError) as exc:
        return {"id": item_id, "state": "UNREADABLE", "awaiting": None,
                "depends_on": None, "path": str(card), "why": str(exc)}
    if not isinstance(document, dict):
        return {"id": item_id, "state": "UNREADABLE", "awaiting": None,
                "depends_on": None, "path": str(card),
                "why": "the card parses into something that is not a mapping"}
    field = contract()["decisions"]
    awaiting = document.get(field["awaiting_field"])
    edges = document.get(field["depends_on_field"])
    return {
        "id": item_id,
        "path": str(card),
        # The Item's own state, read from the Item. `intake/board-contract.yaml`
        # names every Item "the only authority on its own state", so this is a
        # reading rather than a projection -- and a card that declares none says
        # so, instead of borrowing a neighbouring word.
        "state": str(document["state"]) if document.get("state") else "UNSTATED",
        "awaiting": None if awaiting is None else str(awaiting),
        # `None` and `[]` are different answers and only one of them makes it
        # safe to walk past a pending decision: see the contract's
        # `decisions.because_unknown`.
        "depends_on": None if edges is None else [str(x) for x in edges],
        "why": "",
    }


def work(ctx: Context) -> None:
    """The next Activity for the entries a person has already approved."""
    chain = _workchain()
    project = ctx.scope["name"]
    today = chain.load_today(chain.today_path(ctx.records_root, project))
    ctx.step("queue", f"the authored selection is {today['state']}",
             path=today.get("path"), count=len(today.get("items") or ()))
    if today["state"] in ("UNPRODUCED", "UNREADABLE"):
        # The word is the contract's. A run that fell back to a neighbouring
        # stop when this one was missing would render "nobody chose a queue"
        # as "nobody wrote a source list", and `tests/test_recipes.py` holds
        # the whole emitted vocabulary against the contract for that reason.
        ctx.unbound("QUEUE_UNPRODUCED", today["why"])
    if not today["items"]:
        ctx.stop("COMPLETED",
                 f"the selection is {today['state']}: "
                 f"{today.get('why') or 'it names no Item'}")

    entries = [_read_entry(ctx, str(item)) for item in today["items"]]
    unreadable = [e for e in entries if e["state"] == "UNREADABLE"]
    if unreadable:
        ctx.unbound(
            "SOURCE_UNAVAILABLE",
            f"{len(unreadable)} entry/entries could not be read: "
            f"{[e['id'] for e in unreadable]}",
            unreadable=unreadable)

    awaiting = [e["id"] for e in entries if e["awaiting"]]
    ctx.step("classify",
             f"{len(entries)} entry/entries, {len(awaiting)} parked on a person",
             awaiting=sorted(awaiting))

    if not awaiting:
        blocked: list[str] = []
    else:
        undeclared = [e["id"] for e in entries if e["depends_on"] is None]
        if undeclared:
            # §7's clause is that a pending decision blocks ONLY its dependents.
            # That is only true of a graph that was written down; without the
            # edges, proceeding is a guess in both directions.
            ctx.unbound(
                "DECISION_SCOPE_UNKNOWN",
                f"{sorted(awaiting)} is/are parked on a person and "
                f"{sorted(undeclared)} declare no depends_on at all. "
                f"Proceeding would either do work the decision may forbid or "
                f"stop work it does not touch. An entry with no dependents says "
                f"`depends_on: []`; an absent key is a relation nobody stated.",
                awaiting=sorted(awaiting), undeclared=sorted(undeclared))
        blocked = _dependent_closure(entries, roots=set(awaiting))
        ctx.step("classify",
                 f"{len(blocked)} entry/entries are the dependents of a pending "
                 f"decision and are the only ones held back",
                 blocked=sorted(blocked))

    ready = [e["id"] for e in entries if e["id"] not in set(blocked)
             and e["id"] not in set(awaiting)]
    ctx.step("classify", f"{len(ready)} entry/entries are ready", ready=sorted(ready))
    if not ready:
        ctx.stop("NEEDS_DECISION",
                 f"every entry of the selection is parked on a person: "
                 f"{sorted(awaiting)}")

    if ctx.worktree is None:
        ctx.unbound(
            "SOURCE_UNAVAILABLE",
            "no worktree was given, so this window's real delta cannot be "
            "measured. A path that was not given is not a period with no work "
            "in it")
    window = ctx.daily.period_window(ctx.period)
    fallbacks = ctx.daily.Fallbacks()
    delta = ctx.daily.measure_worktree(Path(ctx.worktree), window, fallbacks)
    quay = _quay_block(ctx)
    verdict, because = ctx.daily.progress_verdict(delta, quay)
    ctx.step("progress", f"this window's verdict is {verdict}", because=because,
             worktree=str(ctx.worktree))
    if verdict == ctx.daily.UNKNOWN:
        ctx.unbound("SOURCE_UNAVAILABLE", because)
    if verdict == ctx.daily.NONE:
        # §7's "无新进展" stop. The verdict is the daily report's own, with its
        # three values, so that "a source answered zero" and "no source
        # answered" cannot collapse into one word here after the report went to
        # the trouble of separating them.
        ctx.stop("NO_PROGRESS", because)

    for item in ready:
        if not ctx.work_engine:
            ctx.unbound(
                "WORK_ENGINE_UNBOUND",
                "the next Activity for an approved Item belongs to the "
                "work-engine, and no work-engine was named for this run. "
                "`--work-engine` is where it would be asked; inventing a next "
                "activity here would produce a plan nobody can execute and "
                "that reads like one that can.", ready=sorted(ready))
        if not Path(str(ctx.work_engine)).is_file():
            ctx.unbound(
                "WORK_ENGINE_UNBOUND",
                f"the work-engine named for this run ({ctx.work_engine}) is not "
                f"a file on this node. That is a reading and not a guess: the "
                f"command this loop would run does not exist here, so no "
                f"Activity was asked for.", ready=sorted(ready))
        ctx.spend("advance", ask={
            "reason": "the next Activity for an approved Item, from the work-engine",
            "work_engine": str(ctx.work_engine),
            "item": item,
        })
        ctx.step("advance", f"advanced {item}")


def _dependent_closure(entries: list, *, roots: set) -> set:
    """The transitive dependents of `roots`, over declared edges only."""
    edges = {e["id"]: list(e["depends_on"] or []) for e in entries}
    blocked: set = set()
    changed = True
    while changed:
        changed = False
        for item, needs in edges.items():
            if item in blocked:
                continue
            if any(need in roots or need in blocked for need in needs):
                blocked.add(item)
                changed = True
    return blocked


# ---------------------------------------------------------------------------
# §7 Report -- 汇报
# ---------------------------------------------------------------------------

def report(ctx: Context) -> None:
    """This window's real work, as a document and a receipt. Sends nothing."""
    daily = ctx.daily
    ctx.step("window", f"the period is {ctx.period['id']}",
             start=ctx.period["start"], end=ctx.period["end"])

    if ctx.out_dir is None:
        raise Refused(
            "report writes two files and needs to be told where. `--out-dir` is "
            "required, absolute, and outside this repository -- a report "
            "written into the revision is replaced wholesale by the next one "
            "while looking exactly like one that is kept")
    out_dir = Path(ctx.out_dir)

    # §7's Report stop: "投递未知先 reconcile，不重复发". Read before writing, so
    # that a second run of this period cannot produce a second document for a
    # period somebody may already be holding.
    receipt_path = out_dir / "publication-receipt.json"
    if receipt_path.is_file():
        try:
            previous = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
        if previous is not None:
            period = ((previous.get("report") or {}).get("period") or {})
            delivered = bool((previous.get("publication") or {}).get("delivered"))
            if period.get("start") == ctx.period["start"] and \
                    period.get("end") == ctx.period["end"] and not delivered:
                ctx.unbound(
                    "DELIVERY_UNRECONCILED",
                    f"a receipt for this period already exists at "
                    f"{receipt_path} and does not say the report was delivered "
                    f"(state {((previous.get('publication') or {}).get('state'))!r}). "
                    f"Re-publishing would produce a second document for a "
                    f"period that may already be in somebody's hands.",
                    receipt=str(receipt_path))
            ctx.step("receipts",
                     f"a receipt exists for {period.get('start')}..{period.get('end')} "
                     f"and says "
                     f"{((previous.get('publication') or {}).get('state'))!r}",
                     receipt=str(receipt_path))
    else:
        ctx.step("receipts", f"no receipt at {receipt_path}",
                 receipt=str(receipt_path))

    if ctx.worktree is None:
        ctx.unbound("SOURCE_UNAVAILABLE",
                    "no worktree was given, so the period's work delta cannot "
                    "be read. An absent path is a GAP in the document, and this "
                    "loop does not write a GAP where it could have measured")
    window = daily.period_window(ctx.period)
    fallbacks = daily.Fallbacks()
    work_delta = daily.measure_worktree(Path(ctx.worktree), window, fallbacks)
    quay = _quay_block(ctx)
    verdict, because = daily.progress_verdict(work_delta, quay)
    ctx.step("measure",
             f"the verdict for this window is {verdict}", because=because,
             worktree=str(ctx.worktree),
             quay=str(ctx.run_dir) if ctx.run_dir else None)

    # `focus` is the Item ids this period was aimed at, and only an item-scoped
    # run has one. Passing the project name here would put a name in a field
    # whose reader expects an Item, and the document's own focus block would
    # then say a focus was named when none was.
    document = daily.compose(
        period=window, work_delta=work_delta, quay=quay,
        focus=[ctx.scope["name"]] if ctx.scope["kind"] == "item" else None,
        fallbacks=fallbacks)
    text = daily.render(document, daily.skeleton())
    payload = daily.receipt(
        document, text, out_dir=out_dir, recipient=ctx.recipient,
        channel=ctx.channel, state=ctx.publication_state)
    published = daily.publish(document, text, payload, out_dir)
    ctx.step("compose", f"wrote {published['document']} and {published['receipt']}",
             sha256=published["sha256"])
    ctx.running["outcomes"]["document"] = published["document"]
    ctx.running["outcomes"]["receipt"] = published["receipt"]
    ctx.running["outcomes"]["sha256"] = published["sha256"]
    ctx.running["outcomes"]["publication_state"] = payload["publication"]["state"]
    ctx.running["outcomes"]["progress"] = verdict


# ---------------------------------------------------------------------------

LOOPS = {"perception": perception, "work": work, "report": report}


class Engine:
    """One run of one loop. Owns the record; the loop owns the steps."""

    def __init__(self, *, state_root, scope, period, model=None, transport=None,
                 records_root=None, workspace_root=None, worktree=None,
                 run_dir=None, out_dir=None, recipient=None, channel=None,
                 publication_state=None, sources=None, sources_path=None,
                 work_engine=None, now=None) -> None:
        self.state_root = Path(state_root)
        self.scope = parse_scope(scope)
        self.period = period
        self.transport = transport
        self.model = model
        self.records_root = Path(records_root) if records_root else None
        self.workspace_root = Path(workspace_root) if workspace_root else None
        self.worktree = Path(worktree) if worktree else None
        self.run_dir = Path(run_dir) if run_dir else None
        self.out_dir = Path(out_dir) if out_dir else None
        self.recipient = recipient
        self.channel = channel
        self.publication_state = publication_state
        self.sources = sources
        self.sources_path = sources_path
        self.work_engine = work_engine
        self._now = now

    def now(self) -> str:
        if self._now is not None:
            return self._now() if callable(self._now) else str(self._now)
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def run(self, *, recipe_id: str, budget: int, budget_from: str = "--budget",
            retake: bool = False, revision: str | None = None) -> dict:
        entry = recipe(recipe_id)
        if not isinstance(budget, int) or isinstance(budget, bool) or budget < 0:
            raise Refused(
                f"budget is {budget!r}. It is a count of model steps this run may "
                f"take, and it is required rather than defaulted: a ceiling "
                f"nobody set is one nobody agreed to. `--budget 0` is a complete "
                f"run of every step that does not need a model")
        path = record_path(self.state_root, recipe_id=recipe_id,
                           scope_ref=self.scope["ref"], period_id=self.period["id"])
        previous = read_record(path)

        if previous is not None and previous.get("unreadable"):
            return _refuse(path, previous, "SOURCE_UNAVAILABLE",
                           "the record for this key exists and does not parse. "
                           "Nobody can say what it recorded, so this run will "
                           "not overwrite it")
        state = (previous or {}).get("state")
        if state == DONE and not retake:
            return _already(path, previous)
        if state == CLAIMED and not retake:
            return _refuse(
                path, previous, "IN_FLIGHT_OR_ABANDONED",
                f"a claim for this key was written at "
                f"{(previous or {}).get('claimed_at')} and no outcome followed. "
                f"Whether that run finished its work is not in the record, so "
                f"this is a question for a person or `--retake`")

        # §7's "持久 last-success/cursor". The cursor this run compares against
        # is this SCOPE's, not this key's: a cursor read from the key's own
        # record would only be found on a re-run of the same period, so every
        # new period would see every source as first-seen and reach the model
        # step -- which is the cost the increment gate exists to avoid. Where it
        # came from is written into the record, because "carried from the
        # scope's last success" and "this is the first run of this scope" are
        # different facts and render identically as an empty mapping.
        success = None
        if not ((previous or {}).get("cursor")):
            success = last_success(self.state_root, recipe_id=recipe_id,
                                   scope_ref=self.scope["ref"])
        if (previous or {}).get("cursor"):
            cursor, cursor_from = dict(previous["cursor"]), f"key:{path.name}"
        elif success is not None:
            cursor = dict(success["cursor"])
            cursor_from = (f"scope-last-success:{Path(success['path']).name}"
                           f"@{success.get('finished_at')}")
        else:
            cursor, cursor_from = {}, "no-success-yet"

        running = {
            "schema": SCHEMA_RECORD,
            "recipe": recipe_id,
            "recipe_name": entry.get("name"),
            "scope": self.scope["ref"],
            "period": self.period,
            "key": f"{recipe_id}|{self.scope['ref']}|{self.period['id']}",
            "state": CLAIMED,
            "budget": {"given": budget, "spent": 0, "from": budget_from},
            "model_calls": 0,
            "steps": [],
            "notes": [],
            "outcomes": {},
            "cursor": cursor,
            "would_call": (previous or {}).get("would_call"),
            "claimed_at": self.now(),
            "claimed_by": f"pid:{_pid()}",
            "finished_at": None,
            "stopped_because": None,
            "stopped_why": None,
            "revision": revision,
            "retook_at": (previous or {}).get("retook_at"),
            "previous_state": (previous or {}).get("state") or NEVER_RUN,
            "previous_claims": list((previous or {}).get("previous_claims") or ()),
            "cursor_from": cursor_from,
        }
        if retake and previous is not None:
            running["retook_at"] = self.now()
            running["previous_state"] = previous.get("state")
            if previous.get("state") == CLAIMED:
                # The contract's `records.retake` sentence: the earlier claim
                # is not erased. Overwriting `claimed_at` would leave a record
                # that says this period was attempted ONCE, at the retake's
                # time -- which is the one fact a reader needs after a crash,
                # stated wrongly. So the old claim moves to a list rather than
                # out of the record.
                running["previous_claims"].append({
                    "claimed_at": previous.get("claimed_at"),
                    "claimed_by": previous.get("claimed_by"),
                    "retaken_at": self.now(),
                })
        _write_record(path, running)

        ctx = Context(engine=self, running=running)
        try:
            LOOPS[recipe_id](ctx)
            running["state"] = DONE
            running["stopped_because"] = "COMPLETED"
            running["stopped_why"] = "the finite step list ran to its end"
        except (Stop, Unbound) as stop:
            running["state"] = STOPPED
            running["stopped_because"] = stop.reason
            running["stopped_why"] = stop.why
            running["outcomes"].update(getattr(stop, "outcomes", {}) or {})
        except Exception as exc:  # noqa: BLE001 - a fault is a label, not a crash
            running["state"] = STOPPED
            running["stopped_because"] = "FAULT"
            running["stopped_why"] = f"{type(exc).__name__}: {exc}"

        running["budget"]["spent"] = running["model_calls"]
        running["finished_at"] = self.now()
        if running["stopped_because"] in (None, "COMPLETED"):
            running["cursor"] = running.get("cursor") or {}
        _write_record(path, running)
        running["path"] = str(path)
        running["exit_code"] = (EXIT_OK if running["state"] == DONE
                                else exit_code_for(running["stopped_because"]))
        return running


def _pid() -> int:
    import os
    return os.getpid()


def _already(path: Path, previous: dict) -> dict:
    return {
        "schema": SCHEMA_RECORD,
        "recipe": previous.get("recipe"),
        "scope": previous.get("scope"),
        "period": previous.get("period"),
        "key": previous.get("key"),
        "state": previous.get("state"),
        "previous_state": previous.get("state"),
        "stopped_because": "ALREADY_DONE",
        "stopped_why": (f"this key was completed at "
                        f"{previous.get('finished_at')}. Nothing was done a "
                        f"second time, and no model was called: the key is the "
                        f"record path, so a restart finds the record rather "
                        f"than repeating the work"),
        "finished_at": previous.get("finished_at"),
        "model_calls": 0,
        # NOT `given: 0`. This invocation ran no step, so it had no ceiling of
        # its own -- and `0` would say a ceiling of zero was agreed to, which
        # is a different fact. What the earlier run's ceiling was is in the
        # record already; this is the answer to "what did THIS one spend".
        "budget": {"given": None, "spent": 0, "from": "no-run:ALREADY_DONE"},
        "steps": [],
        "notes": [],
        "outcomes": previous.get("outcomes") or {},
        "cursor": previous.get("cursor") or {},
        "would_call": None,
        "claimed_at": previous.get("claimed_at"),
        "claimed_by": previous.get("claimed_by"),
        "revision": previous.get("revision"),
        "retook_at": previous.get("retook_at"),
        "path": str(path),
        "exit_code": EXIT_OK,
    }


def _refuse(path: Path, previous: dict | None, reason: str, why: str) -> dict:
    return {
        "schema": SCHEMA_RECORD,
        "recipe": (previous or {}).get("recipe"),
        "scope": (previous or {}).get("scope"),
        "period": (previous or {}).get("period"),
        "key": (previous or {}).get("key"),
        "state": (previous or {}).get("state"),
        "previous_state": (previous or {}).get("state") or NEVER_RUN,
        "stopped_because": reason,
        "stopped_why": why,
        "finished_at": (previous or {}).get("finished_at"),
        "model_calls": 0,
        # Same reading as `_already`: no step ran, so no ceiling was spent
        # against and none was this invocation's. `0` here would be the
        # `None`/`0` confusion with the sign flipped.
        "budget": {"given": None, "spent": 0, "from": f"no-run:{reason}"},
        "steps": [],
        "notes": [],
        "outcomes": {},
        "cursor": (previous or {}).get("cursor") or {},
        "would_call": None,
        "claimed_at": (previous or {}).get("claimed_at"),
        "claimed_by": (previous or {}).get("claimed_by"),
        "revision": (previous or {}).get("revision"),
        "retook_at": (previous or {}).get("retook_at"),
        "path": str(path),
        "exit_code": exit_code_for(reason),
    }


def status(*, state_root, scope_ref: str, period: dict) -> dict:
    """Every loop's record for this scope and period, or the absence of one."""
    out = {"schema": "serveros/recipe-status.v1", "scope": scope_ref,
           "period": period, "recipes": {}, "summary": {}}
    counts = {"DONE": 0, "CLAIMED": 0, "STOPPED": 0, NEVER_RUN: 0}
    for entry in recipes():
        path = record_path(state_root, recipe_id=entry["id"],
                           scope_ref=scope_ref, period_id=period["id"])
        document = read_record(path)
        if document is None:
            out["recipes"][entry["id"]] = {"state": NEVER_RUN, "path": str(path),
                                           "means": ("nobody has run this loop "
                                                     "for this key")}
            counts[NEVER_RUN] += 1
            continue
        state = document.get("state")
        counts[state] = counts.get(state, 0) + 1
        out["recipes"][entry["id"]] = {
            "state": state,
            "path": str(path),
            "stopped_because": document.get("stopped_because"),
            "stopped_why": document.get("stopped_why"),
            "finished_at": document.get("finished_at"),
            "model_calls": document.get("model_calls"),
            "cursor_sources": sorted((document.get("cursor") or {})),
        }
    out["summary"] = counts
    out["exit_code"] = (EXIT_OK if counts["CLAIMED"] == 0 and counts[NEVER_RUN] == 0
                        else EXIT_COULD_NOT_ANSWER)
    return out


# ---------------------------------------------------------------------------

def _sentence(text) -> str:
    """A folded YAML scalar as one line, so a terminal render stays readable."""
    return " ".join(str(text).split())


def listing() -> dict:
    """§7's three loops as the contract states them, and nothing measured.

    Deliberately does not touch the node: this answers "what is a recipe", and
    a verb that answered it by reading the state root would be unusable before
    anything has ever run -- which is exactly when a person asks.
    """
    document = contract()
    stops = document["stops"]
    out = {
        "schema": "serveros/recipe-listing.v1",
        "contract": str(CONTRACT),
        "key_formula": document["key"]["formula"],
        "period_id": document["period"]["id"],
        "period_requires_offset": bool(document["period"]["requires_offset"]),
        "scopes": [{"id": scope["id"], "reads": _sentence(scope["reads"]),
                    "unproduced_means": scope["unproduced_means"]}
                   for scope in scopes()],
        "model": {"transports": list(model_transports()),
                  "budget": document["model"]["budget"]},
        "recipes": [],
    }
    for entry in recipes():
        out["recipes"].append({
            "id": entry["id"],
            "name": entry.get("name"),
            "loop": entry.get("loop"),
            "trigger": _sentence(entry.get("trigger")),
            "action": _sentence(entry.get("action")),
            "steps": [{"id": step["id"],
                       "needs_model": bool(step.get("needs_model")),
                       "does": _sentence(step["does"])}
                      for step in entry["steps"]],
            "stops": [{"reason": reason,
                       "normal": bool(stops[reason].get("normal")),
                       "exit_code": exit_code_for(reason),
                       "means": _sentence(stops[reason]["means"])}
                      for reason in entry["stops"]],
        })
    return out


def transport_for(state_root) -> str | None:
    """The model transport this node has bound, or None. Never an invention.

    Two readings, in this order, and the order is the point:

      * the contract's registry. When it is empty -- which is the fact on this
        distribution -- nothing is bound, and no file can bind one. An engine
        that could call anything typed into a config file would be a remote
        execution surface wearing a configuration's clothes.
      * the instance's binding file. A name it carries that is NOT in the
        registry is refused rather than used, so the config cannot widen the
        registry by being edited.
    """
    registry = model_transports()
    path = Path(state_root) / "model-binding.yaml"
    if not path.is_file():
        return None
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError) as exc:
        raise Refused(f"{path} does not parse ({exc}), so no transport is "
                      f"bound. An unreadable binding is not an absent one.")
    name = document.get("transport")
    if name is None:
        return None
    if name not in registry:
        # Reached even when the registry is EMPTY, which is the ordinary case
        # here. A file that names a transport nobody registered is refused
        # rather than ignored: an operator who wrote one believes it took
        # effect, and a run that quietly proceeded unbound would be the silent
        # fallback this tree does not allow.
        raise Refused(
            f"{path} binds transport {name!r}, which is not one of "
            f"{list(registry)}. The registry is the contract's, and a binding "
            f"file does not get to widen it.")
    return name


# ---------------------------------------------------------------------------

def default_model_invoker(transport, ask, ctx):  # pragma: no cover
    """There is no transport, so this is never called.

    It exists as the single name a bound transport would be reached through, so
    that "no model is called" is a statement about a call site that exists and
    is guarded, rather than about a call site that was never written.
    """
    raise Unbound(
        "MODEL_UNBOUND",
        f"the engine reached its model call site with transport {transport!r} "
        f"and this distribution ships no invoker")



# ---------------------------------------------------------------------------
# What `serverosctl recipe …` needs, and nothing else does
# ---------------------------------------------------------------------------
#
# The CLI lives in `runtime/cli.py`; this module is its API. These three
# functions are here rather than there for one reason: each of them is a
# reading of THIS contract, and a second parser in the CLI would be a second
# place for the same reading to drift.

def state_root_for(override=None) -> Path:
    """Where run records go, from the resolver that owns the answer.

    With `override` given, that is the answer -- an isolated run, a test, a
    record written somewhere other than this node. Without it, the state root
    is `runtime/paths.py`'s and the `recipes` namespace must be one the root
    contract DECLARES: a producer that writes a namespace the contract does not
    know about has created a directory that reads as state and is not.

    The declaration is checked BEFORE the node's binding, and the order is the
    one `scripts/verify.py#verify_root` states: the contract is a fact about the
    checkout and is always readable, while the binding is a fact about the
    machine. A caller asking about a checkout should hear the checkout's own
    defect first, and the refusal should not depend on which node ran it.
    """
    if override is not None:
        return Path(override)

    module = _state_root_module()
    declared = _declared_namespaces(module)
    if NAMESPACE not in declared:
        raise Refused(
            f"{ROOT / module.CONTRACT_RELATIVE} declares no {NAMESPACE!r} "
            f"namespace, so a record written there would be undeclared state. "
            f"Declared: {sorted(declared)}")

    paths = _paths().Paths()
    if not paths.bound:
        raise Refused(
            "this node's roots are UNBOUND, so there is no state root to write "
            f"a record into: {list(paths.unconfigured)}. An unbound root is a "
            "template string, and writing into one creates a directory that "
            "looks like a name.")
    return Path(paths.resolve(NAMESPACE_REF)["path"])


def _state_root_module():
    return _load("serveros_runtime_state_root", _HERE / "state_root.py")


def _declared_namespaces(module=None) -> set:
    """Read through `runtime/state_root.py`, not by parsing the YAML here.

    Same reason as `scripts/verify.py`: the contract has one reader, and a
    namespace class this module decided for itself would be a second opinion
    about whether the record it is about to write is authoritative.
    """
    module = module or _state_root_module()
    document = module.load_contract(repo_root=ROOT)
    return set(module.namespace_classes(document))


def bound_schedule(state_root, schedule_id: str) -> dict:
    """One schedule an operator authored, or a refusal.

    §7: the timezone, the window, the recipients, the data scope and the cost
    ceiling "必须真实绑定后才启用，不能从机器名猜用户时间". So there is no default
    schedule and no fallback to the host clock: an id this instance has not
    bound is NO_SCHEDULE_BOUND, which is a gap in the asking rather than a
    window nobody will ever read the record of.
    """
    path = Path(state_root) / "schedules.yaml"
    if not path.is_file():
        raise Unbound(
            "NO_SCHEDULE_BOUND",
            f"schedule {schedule_id!r} was asked for and {path} does not "
            f"exist. This repository enables no timer and authors no schedule: "
            f"the timezone, window, recipients, data scope and budget are bound "
            f"by hand on the instance, and a window this node guessed from its "
            f"own clock would be recorded as though somebody had chosen it.")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError) as exc:
        raise Unbound(
            "NO_SCHEDULE_BOUND",
            f"{path} does not parse ({exc}), so schedule {schedule_id!r} "
            f"cannot be read. An unreadable binding is not an absent one.")
    entry = (document.get("schedules") or {}).get(schedule_id)
    if entry is None:
        raise Unbound(
            "NO_SCHEDULE_BOUND",
            f"{path} binds no schedule {schedule_id!r}. It binds "
            f"{sorted((document.get('schedules') or {}))}.")
    missing = [field for field in required_schedule_fields()
               if not _binds(entry, field)]
    if missing:
        raise Unbound(
            "NO_SCHEDULE_BOUND",
            f"schedule {schedule_id!r} in {path} does not bind {missing}. §7 "
            f"requires {list(required_schedule_fields())} to be real bindings "
            f"before a loop is enabled; a run under a partial schedule would "
            f"take the missing half from this machine.")
    window = _window_of(entry, schedule_id=schedule_id, path=path)
    # `window` LAST, and after the spread: the file carries the text a person
    # wrote, and this carries it already parsed. Spreading `entry` over the
    # parsed pair would leave the string in place under the same key, so a
    # reader would get `"08:00-09:00"` where it expected two times -- and the
    # unpack failure would arrive three functions away from here.
    return {**entry, "id": schedule_id, "path": str(path), "window": window}


def _binds(entry: dict, field: str) -> bool:
    """Whether a schedule actually carries this field, as opposed to failing to.

    `budget: 0` is a VALUE and not an absence -- it is the ordinary ceiling for
    a scheduled pass, and the contract's own model section spells out that a
    zero ceiling is a complete run of every step that needs no model. A
    truthiness check here would refuse the commonest schedule there is, and
    would do it by confusing `0` with `None`, which is the confusion this whole
    tree is built to avoid.
    """
    if field not in entry:
        return False
    value = entry[field]
    if value is None:
        return False
    if field == "budget":
        return (isinstance(value, int) and not isinstance(value, bool)
                and value >= 0)
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return len(value) > 0
    return True


def _window_of(entry: dict, *, schedule_id: str, path) -> tuple:
    """`"HH:MM-HH:MM"` as two `time` objects, or a refusal.

    A window that does not parse is refused rather than skipped: a schedule
    whose window nobody can read is one whose runs would take their boundary
    from wherever the parse gave up.
    """
    text = str(entry.get("window") or "").strip()
    start_text, _, end_text = text.partition("-")
    try:
        start = datetime.strptime(start_text.strip(), "%H:%M").time()
        end = datetime.strptime(end_text.strip(), "%H:%M").time()
    except ValueError as exc:
        raise Unbound(
            "NO_SCHEDULE_BOUND",
            f"schedule {schedule_id!r} in {path} declares window {text!r}, "
            f"which is not `HH:MM-HH:MM` ({exc}). A window nobody can read is "
            f"not a window this loop may take a boundary from.")
    if start > end:
        raise Unbound(
            "NO_SCHEDULE_BOUND",
            f"schedule {schedule_id!r} in {path} declares window {text!r}, "
            f"which starts after it ends. An overnight window is a different "
            f"object and this contract does not have one.")
    return start, end


def period_from_args(args, state_root: Path, *, now=None) -> dict:
    """The window this run is about, or a refusal. Never a default.

    Two ways in and no third: an explicit pair, or a schedule this instance has
    bound. Both end at `parse_period`, so the record's window is the same shape
    either way and carries the offset it was given.

    The scheduled path takes the DATE from the current instant but the OFFSET
    from the schedule's own `timezone`, which is the whole reason a schedule is
    a binding and not a convenience: a window evaluated in this process's
    timezone would be a window nobody chose, and on a shared machine the
    process's timezone is a fact about the machine. The instant is a parameter
    so that a test can say which day it is rather than waiting for one.
    """
    start, end = args.period_start, args.period_end
    source = "given"
    if args.schedule:
        entry = bound_schedule(state_root, args.schedule)
        start, end = _scheduled_window(entry, when=now)
        source = f"schedule:{args.schedule}"
    if not start or not end:
        raise Refused(
            "a period is required and is never chosen here. Give "
            "--period-start and --period-end (ISO-8601 with an offset), or "
            "--schedule <id> for a window this instance has bound. §7's record "
            "is about the period somebody chose, and a window this run picked "
            "for itself would read exactly like one that was chosen.")
    period = parse_period(start=start, end=end)
    period["source"] = source
    return period


def budget_from_args(args, state_root: Path) -> tuple:
    """This run's ceiling, and where the number came from.

    Two ways in and no third, mirroring the period. An explicit `--budget` is
    the answer. Otherwise a bound schedule's own `budget` is, because §7 lists
    the ceiling among the fields a schedule binds (`schedules.required_fields`)
    and a scheduled run that had to be told its ceiling again on the command
    line is a schedule whose `budget` field nobody reads -- the definition of a
    decorative required field.

    Neither path is a default. A run with no `--budget` and no schedule is
    refused, because the contract's `required: true, default: none` is a
    statement about the number and not a hole to fill in.

    WHERE IT CAME FROM travels with it into the record. "The ceiling this
    operator typed for this run" and "the ceiling this schedule binds" render
    identically as an integer, and a later reader deciding whether a spent
    budget was the agreed one needs the difference.
    """
    given = getattr(args, "budget", None)
    if given is not None:
        return given, "--budget"
    if not getattr(args, "schedule", None):
        raise Refused(
            "a budget is required and is never defaulted. Give --budget "
            "<model steps> (0 is a complete run of every step that needs no "
            "model), or --schedule <id> for a schedule whose own budget is "
            "bound. §7: a ceiling nobody set is one nobody agreed to.")
    # `bound_schedule` already refused a schedule whose budget does not bind --
    # it is one of the required fields -- so this is a read, not a second check.
    entry = bound_schedule(state_root, args.schedule)
    return entry["budget"], f"schedule:{args.schedule}"


def _scheduled_window(entry: dict, *, when=None) -> tuple:
    """One schedule's window on the day `when` falls in, in the schedule's zone.

    A timer fires at an instant; the period is the window that instant is in,
    and the window belongs to the schedule's timezone. So the date comes from
    `when` as seen in that zone, and the offsets come from that zone too --
    which is what makes a schedule bound to `Asia/Tokyo` produce
    `+09:00` regardless of where the process is running.
    """
    name = str(entry["timezone"])
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(name)
    except Exception as exc:  # noqa: BLE001 - a missing tz database is a refusal
        raise Unbound(
            "NO_SCHEDULE_BOUND",
            f"schedule {entry['id']!r} names timezone {name!r} and this node "
            f"cannot resolve it ({exc}). A run that fell back to the host's "
            f"clock would write a period nobody chose.")
    moment = when or datetime.now(zone)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=zone)
    local = moment.astimezone(zone)
    start_clock, end_clock = entry["window"]
    start = datetime.combine(local.date(), start_clock, tzinfo=zone)
    end = datetime.combine(local.date(), end_clock, tzinfo=zone)
    return start.isoformat(), end.isoformat()
