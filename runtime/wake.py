"""The wake half of a continuation: what, on this node, resumes a checkpoint.

`runtime/continuation.py` writes the record and answers «will anything wake this».
It says its own `RECEIVERS` is «the registry the wake half will fill», and that
until something fills it the empty tuple is the measurement. This module is that
half, and `fill_registry` below is the only writer of that name.

WHAT A RECEIVER IS, AND WHAT IT IS NOT
--------------------------------------
A receiver is not a planner and not a daemon. It reads four facts and acts on
nothing else:

    1. is this checkpoint still the one that owns its Item      (the store)
    2. has the thing it waits for happened yet                  (continuation.wait_state)
    3. does a grant that STILL authorizes now cover this Item    (context/delegation)
    4. has this epoch already been woken, and how many times may it be

When all four say yes it starts ONE native Parent, hands it the checkpoint and
the delegation it was verified under, and records a receipt. It never decides
what the work is, never chooses the Parent's working directory, never judges
whether the grant's SCOPE is right for the task, and never receives a child's
result. `docs/daily-work-cycle.md` §17.4 is the sentence it must not break from
the other side: «没有 receiver 时准确显示"checkpoint已保存，待续"，不能伪造后台
运行» — so a receiver that cannot start a Parent must say so rather than report
the checkpoint as carried.

WHY IT IS A ROUTINE AND NOT A TICK
----------------------------------
The fabric already has the shape for «something that must happen on a cadence,
bounded, and reportable»: a oneshot unit rendered from the revision, with a
timer beside it (`serveros-alert-sync`, `serveros-daily`). A tick inside
`serverosd` would be a second one, and it would be worse than a second one: the
daemon would be starting session-shaped work in the same loop that evaluates
detectors, and every tick would become a possible writer of authority. The
daemon's own tick reads neither admissions nor checkpoints today, and that is
the property this module keeps rather than the gap it fills.

AND THE RECEIVER CANNOT MINT AN ACTOR, WHICH IS WHY IT DOES NOT TRY. Every
mutating session action on this node resolves its actor from a `context_ref`,
and the one gate that mints a ref refuses to take a `report` from its caller. A
routine that is a timer has no person behind it and could never mint one. What
it can do — and what §9.38.9 records as the whole of the design — is CARRY a
delegation a person already made: `context/delegation.py`'s sentence is that «a
grant is a scope on a trusted context, and it authorizes a cycle only while that
context still IS one». So the briefing below names the context the grant derives
from, and the Parent that reads it re-checks that context for itself. The
receiver moves authority; it does not create it, and a node where no
delegation authorizes is a node where this routine wakes nothing.

AT MOST ONE PARENT PER ATTEMPT, AND A FINITE NUMBER OF ATTEMPTS
---------------------------------------------------------------
Two Parents on one epoch is the failure the store's «one continuation owner per
scope» rule exists to prevent, so the bound is not a nicety. It comes from the
grant, in the grant's own words: `budget.max_attempts` is how many times the
delegated cycle may be attempted and `budget.wall_seconds` is how long one may
take. The receiver does not invent a retry policy; it spends the one it was
handed. `runtime/bounded_invocation.invoke_once` supplies the other half — one
action key is one receipt, a second call under the same key with the same input
REPLAYS rather than executes, and a receipt lock another wake still holds is
refused rather than waited for.

Recovery from a Parent that exited without taking its Item over is therefore a
real case and not a hole: the receipt for that attempt is terminal, which under
`invoke_once` means the process is gone (it waits synchronously and kills the
group on its way out), the checkpoint is still live and still ready, and the
next tick spends the next attempt. A receipt still reading `STARTED` is the one
case that is NOT retried: that module's own comment says a crash there «requires
reconciliation, never blind replay», and a wake that spawned a second Parent
under it would be exactly the blind replay it refuses.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

SCHEMA = "serveros/continuation-wake.v1"

#: The receiver as it appears in `continuation.receivers()` and in a briefing.
#: `routine:` and not `service:` because what is registered is the thing that
#: runs on a cadence, and a reader looking this up in `systemctl` needs the unit
#: names below rather than this string.
RECEIVER_ID = "routine:serveros-continuation-wake"
SERVICE_NAME = "serveros-continuation-wake.service"
TIMER_NAME = "serveros-continuation-wake.timer"

#: The three things this routine can say about one checkpoint.
#:
#: `WAKE` is the only one that starts anything, and it is a claim about ONE
#: checkpoint — never a claim that the Item is now carried. `HOLD` is a wait that
#: has not ended. `REFUSE` is everything the routine will not do, and every
#: refusal carries the reason below, because a receiver that printed «nothing to
#: do» for a checkpoint it could not authorize would read exactly like a node
#: with nothing to wake.
PLAN_ACTIONS = ("WAKE", "HOLD", "REFUSE")

#: Why one checkpoint got the answer it got. Closed, and each entry is a fact
#: about a different object, so that no two of them can be printed as one.
#:
#: `SUPERSEDED` AND `PARKED` ARE THE TWO WAYS OUT OF THE LIVE SET and they are
#: opposite facts, which is why neither is inferred from the other: a later epoch
#: carries the Item, or a person set it down and nobody does. `NOT_LIVE` is the
#: net under both — a record the store stopped answering for in a way neither
#: field names — and it exists so that a store change becomes a refusal with an
#: honest sentence instead of a wrong reason.
PLAN_REASONS = (
    "READY",
    "SUPERSEDED",
    "PARKED",
    "NOT_LIVE",
    "WAIT_OPEN",
    "WAIT_UNANSWERABLE",
    "NO_AUTHORIZING_GRANT",
    "NO_ATTEMPT_BUDGET",
    "NO_WALL_BUDGET",
    "BUDGET_EXHAUSTED",
    "WAKE_IN_FLIGHT",
    "ATTEMPTS_EXHAUSTED",
    "BRIEFING_OVER_BOUND",
)

#: What became of one attempt to start a Parent. `REPLAYED` is not a failure and
#: is not a second start either: it is `invoke_once` answering that this exact
#: action key already carries this exact input, which is the receipt doing the
#: only job it has.
WAKE_RESULTS = ("WOKEN", "REPLAYED", "REFUSED", "NOT_STARTED")

#: One bounded sentence out, and a briefing that is a bounded document. The same
#: rule `continuation.MAX_NEXT_ACTION` states one module over: a long one is the
#: session being copied, and a session copied is a session replayed. Larger than
#: that bound because a briefing carries the checkpoint's own fields and not only
#: one sentence, and still far below a transcript.
MAX_BRIEFING = 4000

#: `runtime/bounded_invocation.invoke_once`'s own key rule, restated here so a
#: derived key this module cannot use is refused where it is derived rather than
#: one call later. The name is repeated and not imported because the two modules
#: must agree on it and a shared constant would let one of them change it alone.
KEY_RULE = re.compile(r"[a-zA-Z0-9_-]{1,100}")

#: One attempt's key, parsed back out of the receipts directory. The receipts
#: root belongs to `invoke_once` and may hold keys this module never wrote, so
#: the count is a match and not «every file in there».
ATTEMPT_KEY = re.compile(
    r"^continuation-wake-(?P<item>.+)-e(?P<epoch>\d+)-a(?P<attempt>\d+)$")


class WakeRefused(RuntimeError):
    """This module was asked for something it cannot answer honestly."""


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _continuation():
    """`runtime/continuation.py`, under the ONE name its other loaders use.

    The name matters and is not decorative: `runtime/cli.py` loads this file
    under `serveros_runtime_continuation`, and `fill_registry` writes to the
    module object's `RECEIVERS`. Two names would be two module objects and this
    module would fill a registry nobody reads.
    """
    return _load("serveros_runtime_continuation", _HERE / "continuation.py")


def _delegation():
    return _load("serveros_context_delegation", _ROOT / "context" / "delegation.py")


def _cycle():
    """`intake/daily_cycle.py`, under the ONE name its other loaders use.

    `standing_reason` is CALLED here and never re-implemented. It is the tree's
    single answer to «is this grant live, and is it in force at this instant» —
    `runtime/cli.py`'s reading path calls it for the same two judgements — and a
    second comparison of `revoked`/`valid_from`/`expires_at` would be a second
    answer to whether a delegation is current, which is the failure this tree
    names more often than any other.
    """
    return _load("serveros_intake_daily_cycle", _ROOT / "intake" / "daily_cycle.py")


def _instant(raw: str):
    """`raw` as an aware datetime, or None when it is not one.

    Not a validation of the caller's clock — `now` is an argument everywhere in
    this module. It is what keeps an unreadable instant from arriving as a
    traceback: the comparison `standing_reason` makes needs an aware datetime on
    both sides, and an instant nobody can read is not an instant at which
    authority can be established. `None` is therefore reported as a grant that
    does not stand, which is the direction that wakes nothing.
    """
    try:
        parsed = datetime.datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _invocation():
    return _load("serveros_runtime_bounded_invocation",
                 _HERE / "bounded_invocation.py")


# ---------------------------------------------------------------------------
# What is installed here — measured, not declared
# ---------------------------------------------------------------------------


def unit_root(unit_dir=None) -> Path:
    """Where this user's systemd units live.

    `XDG_CONFIG_HOME` if it is set and `~/.config` otherwise, which is the rule
    systemd itself applies to a `--user` manager. The install path
    (`scripts/release.py continuation-wake --unit-dir`) is a parameter and this
    is only its default, so a test can measure a directory of its own.
    """
    if unit_dir is not None:
        return Path(unit_dir)
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "systemd" / "user"


def armed(unit_dir=None) -> bool:
    """Whether the timer is equipped, read off the same symlink enable writes.

    This is the file `systemctl --user enable` creates, read rather than asked
    for. Asking `systemctl` would make this reading depend on a bus and a manager
    that a oneshot on a timer may well be running without, and the question is
    «is this unit in the wanted set» — a fact about a directory.
    """
    return (unit_root(unit_dir) / "timers.target.wants" / TIMER_NAME).exists()


def launcher_argv(unit_dir=None) -> list:
    """The Parent command the installed unit would run, or `[]`.

    An armed timer whose service starts a command with no Parent to start is not
    a receiver: it fires on cadence and wakes nothing. So the ExecStart line is
    read here rather than assumed, and an unrendered `@…@` placeholder counts as
    absent — a unit installed without a launcher must not read as one that has
    one.

    ONE EXECUTABLE AND NO ARGUMENTS, because that is what a placeholder in a unit
    file can carry without a quoting rule. `ExecStart=` is split by systemd and
    this reads it back out of the rendered text: a command line that has to
    survive both readers correctly is a command line that will one day be read
    differently by one of them. An install that needs arguments puts them in the
    executable — a wrapper script — and `cmd_continuation_wake` says so when it
    refuses anything that is not a path.
    """
    path = unit_root(unit_dir) / SERVICE_NAME
    if not path.is_file():
        return []
    line = ""
    for candidate in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if candidate.startswith("ExecStart="):
            line = candidate[len("ExecStart="):]
    if not line:
        return []
    tokens = line.split()
    for index, token in enumerate(tokens):
        if token == "--parent":
            value = tokens[index + 1] if index + 1 < len(tokens) else ""
        elif token.startswith("--parent="):
            value = token.split("=", 1)[1]
        else:
            continue
        return [value] if value and "@" not in value else []
    return []


def equipment(unit_dir=None) -> dict:
    """Both halves of «is a receiver installed here», reported apart.

    `armed` and `wired` are separate answers on purpose: a timer with no launcher
    and a launcher with no timer are different repairs, and `installed` requires
    both because a receiver that cannot start a Parent is not one. This is the
    direction the error has to lean — under-reporting a receiver makes a reader
    conservative, and over-reporting one makes `continuation_state` say
    `RECEIVER_INSTALLED` about a node that will never wake anything, which is the
    sentence §17.4 forbids.
    """
    argv = launcher_argv(unit_dir)
    equipped = armed(unit_dir)
    return {"receiver": RECEIVER_ID, "unit_dir": str(unit_root(unit_dir)),
            "service": SERVICE_NAME, "timer": TIMER_NAME,
            "armed": equipped, "wired": bool(argv), "launcher": argv,
            "installed": bool(equipped and argv)}


def installed(unit_dir=None) -> tuple:
    """What belongs in `continuation.RECEIVERS` on this node, today."""
    return (RECEIVER_ID,) if equipment(unit_dir)["installed"] else ()


def fill_registry(continuation_module=None, *, unit_dir=None) -> tuple:
    """Fill the wake half's registry from what is measured, and return it.

    The single writer of `continuation.RECEIVERS`, which is what that module's
    docstring asks for when it calls the name «the registry the wake half will
    fill». Writing it from a reader (`work continuations`, which prints the
    answer) and from the routine (which is the answer) is deliberate: both are
    asking «is anything on this node going to wake a checkpoint», and an answer
    that depended on which one asked would be two answers.

    Assigned rather than appended, so a second call reflects the units NOW rather
    than accumulating what was ever installed. A registry that kept an uninstalled
    receiver would be reporting a background run that has been removed.
    """
    module = _continuation() if continuation_module is None else continuation_module
    module.RECEIVERS = installed(unit_dir)
    return module.RECEIVERS


# ---------------------------------------------------------------------------
# Who may be woken — the delegation, and the bound the delegation carries
# ---------------------------------------------------------------------------


def receipts_root(store_path, receipts=None) -> Path:
    """Where this routine's receipts live, beside the store's own state.

    Derived from the store path rather than added to `runtime/paths.py`'s closed
    set of state names, which is the same move `runtime/card_delivery.py` makes
    for its ledger: this directory is one consumer's bookkeeping, and a name in
    that closed set is a name every surface would have to be told about.
    """
    if receipts is not None:
        return Path(receipts)
    return Path(store_path).parent.parent / "wake" / "receipts"


def action_key(work_item: str, epoch, attempt: int) -> str:
    """This attempt's idempotency key, refused here if it is not a legal one.

    Derived and not accepted from a caller, because `invoke_once` binds one
    receipt to one key and a caller-chosen key would let two wakes of one epoch
    both be «the first».

    AND IT MUST READ BACK. `attempts_recorded` finds this attempt's receipt by
    parsing the file name with `ATTEMPT_KEY`, so a key this function can WRITE
    and that parser cannot READ is an attempt whose receipt is never found —
    which makes the attempt number stick at 1 and turns every retry into a
    replay of the first one. The two are therefore checked against each other
    here rather than trusted to stay in step: the round trip is the property,
    so the round trip is what is asserted. This is not hypothetical — the
    parser began as `[A-Za-z0-9_]+` for the Item, which cannot read back the
    `SRV-0001`-shaped names every Item in this product actually has.
    """
    key = f"continuation-wake-{work_item}-e{int(epoch)}-a{int(attempt)}"
    if not KEY_RULE.fullmatch(key):
        raise WakeRefused(
            f"the action key derived for {work_item} epoch {epoch} attempt "
            f"{attempt} is {key!r}, which is not one `invoke_once` accepts "
            f"({KEY_RULE.pattern}). The Item's own name is what has to change, "
            f"because a key this module mangled would be a key no receipt can "
            f"be found under again")
    match = ATTEMPT_KEY.match(key)
    if (not match or match.group("item") != str(work_item)
            or int(match.group("epoch")) != int(epoch)
            or int(match.group("attempt")) != int(attempt)):
        raise WakeRefused(
            f"the action key {key!r} cannot be read back into the Item "
            f"{work_item!r}, epoch {epoch} and attempt {attempt} it was derived "
            f"from, so this attempt's receipt could never be found again. An "
            f"Item name containing «-e<epoch>-a<attempt>» is the cause and has "
            f"to change, because an attempt nobody can count is an attempt that "
            f"is replayed forever")
    return key


def authority(grants: dict, contexts: dict, *, item: str, now: str) -> dict:
    """Which recorded grants still authorize, and which of those cover this Item.

    The verdict on each grant is `context/delegation.envelopes`' and is copied
    from it whole. What this function adds is one comparison, and it is the
    narrowest one that makes a wake defensible: the grant must NAME the Item.

    IT DOES NOT JUDGE THE GRANT'S SCOPE, and that is deliberate rather than
    incomplete. Whether a grant's `projects`, `actions` and `scope_digests` suit
    the work about to be done is `intake/daily_cycle.grant_reason`'s question,
    asked with a board row this routine has no reason to go and find — and a
    second judge of one question is how the two answers start disagreeing. What
    is decided here is only «may this routine carry a wake for THIS Item», and a
    grant that names no Item cannot be used to answer it: a checkpoint records no
    project, so a project-scoped grant is one this routine could only match by
    guessing.

    IT DOES JUDGE WHETHER THE GRANT IS IN FORCE, and that is not scope. The
    distinction is the one `runtime/cli.py`'s reading path already draws when it
    calls `intake/daily_cycle.standing_reason` beside this module's verdict:
    «is there a live grant here, and is it in force now» is a question about the
    record, not about the work, and `derivation` answers a third one — whether
    the context at its root still authorizes. `derivation`'s own docstring
    assigns the window and `revoked` elsewhere and this module used not to ask
    anybody, so a grant a person had since REVOKED was reported AUTHORIZED,
    entered the envelope map, and would have carried a wake. Reusing
    `standing_reason` rather than comparing the fields here keeps one answer to
    that question; the envelope is what is handed to it, because a stored grant
    carries its ref under `grant_ref` and `standing_reason` reads `ref`.

    The three groups are reported separately and never merged into «no grant» —
    `delegation`'s own module docstring makes that argument for its four states
    and it holds again one level down: «nothing was ever recorded», «what was
    recorded does not authorize now», «what authorizes is no longer in force»
    and «what is in force does not name this Item» are four different repairs.
    """
    delegation = _delegation()
    report = delegation.envelopes(grants, contexts, now=now)
    stands = _cycle().standing_reason
    at = _instant(now)

    covers: list = []
    others: list = []
    lapsed: list = []
    for row in report["rows"]:
        if row["state"] != "AUTHORIZED":
            continue
        ref = row["grant_ref"]
        # Present by construction: `envelopes` fills the map from the same
        # branch that sets this row's state to AUTHORIZED, keyed by this ref.
        envelope = report["envelopes"][ref]
        because = ("CLOCK_UNREADABLE" if at is None
                   else stands(envelope, now=at))
        if because is not None:
            # NOT «the delegation does not authorize»: the root may be perfectly
            # good and the person's decision to end it is what this is. The
            # reason is copied from `standing_reason` and not re-worded.
            lapsed.append({"grant_ref": ref, "because": because})
            continue
        if item in [str(entry) for entry in (row.get("items") or [])]:
            covers.append(ref)
        else:
            others.append(ref)
    return {"authorizing": sorted(covers), "not_covering": sorted(others),
            "not_standing": sorted(lapsed, key=lambda entry: entry["grant_ref"]),
            "envelopes": report["envelopes"], "rows": report["rows"],
            "summary": report["summary"]}


def _positive(value) -> int | None:
    """One whole number greater than zero, or None. `True` is not one.

    `bool` is an `int` in Python and a budget of `True` would otherwise be read as
    a budget of 1, which is the kind of accident that turns a refusal into a wake.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def spending(budget, *, item: str, grant_ref: str) -> dict:
    """The two numbers this routine needs from a grant, and why each is needed.

    `max_attempts` is the bound on wakes and `wall_seconds` is the bound on the
    Parent. Both are read because both are SPENT here, and both are required: a
    receiver that could not bound itself would either wake an unbounded number of
    Parents or start one with no deadline, and which of those it did would depend
    on nothing a person decided. The third check is `exhausted`, copied from
    `grant_reason`'s reading of the same field rather than re-derived.
    """
    reason = None
    if not isinstance(budget, dict):
        budget = {}
    allowed = _positive(budget.get("max_attempts"))
    wall = _positive(budget.get("wall_seconds"))
    exhausted = budget.get("exhausted") is True
    if allowed is None:
        reason = "NO_ATTEMPT_BUDGET"
    elif wall is None:
        reason = "NO_WALL_BUDGET"
    elif exhausted:
        reason = "BUDGET_EXHAUSTED"
    return {"reason": reason, "attempts_allowed": allowed, "wall_seconds": wall,
            "grant_ref": grant_ref, "item": item}


# ---------------------------------------------------------------------------
# What has already been tried — read out of the receipts, not remembered
# ---------------------------------------------------------------------------


def attempts_recorded(root, *, work_item: str, epoch) -> dict:
    """Every attempt already recorded for this Item at this epoch.

    Read from `invoke_once`'s own receipt files rather than from a counter this
    module keeps, because a counter is a second record of a fact one file
    already carries — and the file is the one that survives being restarted,
    moved, or run by a different process than the one that wrote it.

    A receipt that still reads `STARTED` is counted apart and is the one thing
    that stops a wake outright. `invoke_once` writes that status BEFORE it
    spawns, so a receipt left reading `STARTED` is either a wake running right
    now or one whose writer died mid-flight — and the module's own comment says
    the second «requires reconciliation, never blind replay». Neither is answered
    by starting a second Parent.
    """
    root = Path(root)
    found: dict = {}
    in_flight: list = []
    if root.is_dir():
        for path in sorted(root.glob("continuation-wake-*.json")):
            match = ATTEMPT_KEY.match(path.stem)
            if not match:
                continue
            if match.group("item") != work_item or int(match.group("epoch")) != int(epoch):
                continue
            try:
                receipt = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # A receipt this reading cannot parse is a receipt it cannot
                # count. Counted as in-flight rather than skipped: skipping it
                # would let the next attempt number collide with a key that has
                # a file behind it, and `invoke_once` would then refuse the spawn
                # with a message about a different problem.
                in_flight.append({"attempt": int(match.group("attempt")),
                                  "receipt": str(path), "status": "UNREADABLE"})
                continue
            entry = {"attempt": int(match.group("attempt")),
                     "receipt": str(path),
                     "status": receipt.get("status"),
                     "started_at": receipt.get("started_at"),
                     "finished_at": receipt.get("finished_at"),
                     "process_exit_code": receipt.get("process_exit_code"),
                     "effect": receipt.get("effect")}
            if entry["status"] == "STARTED":
                in_flight.append(entry)
            else:
                found[entry["attempt"]] = entry
    return {"recorded": found, "in_flight": in_flight,
            "terminal": len(found), "latest": found[max(found)] if found else None}


# ---------------------------------------------------------------------------
# The decision, one checkpoint at a time
# ---------------------------------------------------------------------------


def _row(record: dict, wait: dict, action: str, reason: str, says: str, **extra) -> dict:
    row = {"checkpoint_ref": record.get("checkpoint_ref"),
           "work_item": record.get("work_item"), "epoch": record.get("epoch"),
           "parent_owner": record.get("parent_owner"),
           "next_safe_action": record.get("next_safe_action"),
           "wait_state": wait.get("state"), "action": action, "reason": reason,
           "says": says,
           "authorized_by": None, "grants_considered": [], "grants_not_covering": [],
           "action_key": None, "attempt": None, "attempts_terminal": 0,
           "attempts_allowed": None, "wall_seconds": None, "briefing": None}
    row.update(extra)
    return row


def plan(control, *, now: str, item: str | None = None, unit_dir=None,
         receipts=None, continuation_module=None) -> dict:
    """One row per checkpoint: what should happen to it, and why.

    EVERY RECORD GETS A ROW, including the ones nothing will ever wake. A reader
    with no row for a record cannot tell «this routine has nothing to do» from
    «this routine cannot see it», and that difference is the whole of §17.4's
    refusal to let a saved checkpoint look like a background run. What a
    disposition changes is therefore the REASON in the row and never the row's
    existence.

    Every fact comes from a store or from a file: which records are candidates
    from `live_checkpoints`, with `parked_checkpoints` beside it because the two
    ways out of that set are different facts and the row has to say which one it
    found; the wait from `continuation.wait_state`, the authority from
    `context/delegation.envelopes`, the attempts from the receipts. Nothing here is
    remembered between runs, which is what lets the same call be made by a timer
    with no memory and by a person at a terminal and give one answer.
    """
    continuation = _continuation() if continuation_module is None else continuation_module
    held = control.checkpoints()
    admissions = control.admissions()
    grants = control.grants()
    contexts = control.contexts()
    live = set(control.live_checkpoints())
    parked = set(control.parked_checkpoints())
    root = receipts_root(control.path, receipts)

    rows: list = []
    for ref in sorted(held):
        record = held[ref]
        if item is not None and record.get("work_item") != item:
            continue
        wait = continuation.wait_state(record, admissions=admissions, now=now)
        if ref not in live:
            # TWO FACTS PUT A RECORD OUTSIDE THE LIVE SET AND THEY ARE NOT THE
            # SAME SENTENCE, so both are asked of the STORE — the set that decides
            # and the collection that says which way out it was — rather than one
            # of them being inferred from the set's membership.
            #
            # A superseded record is not a candidate: `continuation_state` answers
            # «who carries this» before «what would wake it», and a wake of a
            # replaced epoch is a second Parent on work that has changed hands.
            #
            # A parked record is not a candidate either, and its reason is the
            # opposite one: no epoch replaced it and nobody carries it — a person
            # set it down on purpose. It is answered here, BEFORE the wait below
            # and not after it, because a park is a statement about the work and
            # not a wait that has not finished: a parked record whose window has
            # long passed must not be reported as ready to resume, and one whose
            # window is still open must not be reported as merely held. The wake
            # routine is the surface that used to go on evaluating exactly these
            # records once a minute, which is what made «refused, no authorizing
            # grant» — the sentence a genuinely blocked Item produces — unreadable.
            if record.get("superseded_by"):
                rows.append(_row(record, wait, "REFUSE", "SUPERSEDED",
                                 f"{ref} was taken over by "
                                 f"{record.get('superseded_by')}; a later epoch carries "
                                 f"this Item and this record is what was carried from"))
            elif ref in parked:
                rows.append(_row(record, wait, "REFUSE", "PARKED",
                                 f"{ref} was parked at "
                                 f"{record.get('parked_at')} by "
                                 f"{record.get('parked_by')} — "
                                 f"{record.get('parked_reason')}. A park is a person "
                                 f"setting the work down, so it is answered before the "
                                 f"wait this record was filed with: nothing here wakes "
                                 f"it, and `work unpark` is what puts it back in the "
                                 f"set this routine reads"))
            else:
                # UNREACHABLE BY CONSTRUCTION, and it is a branch rather than an
                # `assert` because of what the alternative would print. The live
                # set excludes exactly the two facts above, so a record here is
                # one this store stopped answering for in a way neither field
                # names — and a row that said «superseded» or «parked» about it
                # would be this routine inventing a disposition for a store state
                # it does not understand. Refusing is the fail-closed half; the
                # sentence says what it knows and no more.
                rows.append(_row(record, wait, "REFUSE", "NOT_LIVE",
                                 f"{ref} is not in the set `live_checkpoints` "
                                 f"answers for, and this record carries neither a "
                                 f"successor nor a park: something outside this "
                                 f"routine's vocabulary has stopped it being a "
                                 f"candidate, and nothing here starts work it cannot "
                                 f"account for"))
            continue
        if wait["state"] == "WAIT_OPEN":
            rows.append(_row(record, wait, "HOLD", "WAIT_OPEN", wait["says"]))
            continue
        if wait["state"] == "WAIT_UNANSWERABLE":
            rows.append(_row(record, wait, "REFUSE", "WAIT_UNANSWERABLE",
                             wait["says"]))
            continue

        work_item = str(record.get("work_item"))
        verdict = authority(grants, contexts, item=work_item, now=now)
        considered = (verdict["authorizing"] + verdict["not_covering"])
        if not verdict["authorizing"]:
            # One clause per distinct fact, because they are distinct repairs:
            # a store with nothing in it, a grant whose delegation has gone, a
            # grant that is recorded and no longer in force, and one that is in
            # force and names other work. A single sentence for all four is what
            # `authority`'s docstring refuses, and the counts are kept apart
            # from the reasons for the same purpose.
            outside = {state: count for state, count in
                       (verdict["summary"]["states"] or {}).items()
                       if state != "AUTHORIZED"}
            clauses: list[str] = []
            if outside:
                clauses.append(f"{sum(outside.values())} recorded grant(s) are "
                               "outside the map: "
                               + ", ".join(f"{state} {count}"
                                           for state, count in sorted(outside.items())))
            if verdict["not_standing"]:
                clauses.append(", ".join(
                    f"{entry['grant_ref']} is recorded and {entry['because']}"
                    for entry in verdict["not_standing"]))
            if verdict["not_covering"]:
                clauses.append(f"{len(verdict['not_covering'])} grant(s) do "
                               f"authorize now and name no Item "
                               f"({', '.join(verdict['not_covering'])}), and a "
                               f"checkpoint records no project for this routine "
                               f"to match one against")
            detail = ("; ".join(clauses) if clauses
                      else "the store records no grant that authorizes now")
            rows.append(_row(record, wait, "REFUSE", "NO_AUTHORIZING_GRANT",
                             f"nothing authorizes waking {work_item}: {detail}",
                             grants_considered=considered,
                             grants_not_covering=verdict["not_covering"]))
            continue

        grant_ref = verdict["authorizing"][0]
        envelope = verdict["envelopes"][grant_ref]
        spend = spending(envelope.get("budget"), item=work_item, grant_ref=grant_ref)
        attempts = attempts_recorded(root, work_item=record.get("work_item"),
                                     epoch=record.get("epoch"))
        common = {"authorized_by": grant_ref, "grants_considered": considered,
                  "grants_not_covering": verdict["not_covering"],
                  "attempts_terminal": attempts["terminal"],
                  "attempts_allowed": spend["attempts_allowed"],
                  "wall_seconds": spend["wall_seconds"]}
        if spend["reason"]:
            rows.append(_row(record, wait, "REFUSE", spend["reason"],
                             _budget_sentence(spend, grant_ref), **common))
            continue
        if attempts["in_flight"]:
            latest = attempts["in_flight"][-1]
            rows.append(_row(record, wait, "REFUSE", "WAKE_IN_FLIGHT",
                             f"an attempt for {record.get('work_item')} epoch "
                             f"{record.get('epoch')} is recorded as "
                             f"{latest['status']} and never recorded an end. "
                             f"`invoke_once` writes that status before it spawns, "
                             f"so this is a wake running now or one whose writer "
                             f"did not survive — and its own rule is that the "
                             f"second requires reconciliation rather than blind "
                             f"replay. Nothing was started",
                             **common))
            continue
        attempt = attempts["terminal"] + 1
        if attempt > spend["attempts_allowed"]:
            rows.append(_row(record, wait, "REFUSE", "ATTEMPTS_EXHAUSTED",
                             f"{attempts['terminal']} of the "
                             f"{spend['attempts_allowed']} attempt(s) "
                             f"{grant_ref} allows have been recorded for "
                             f"{record.get('work_item')} epoch "
                             f"{record.get('epoch')}, so this routine will not "
                             f"wake it again. Whether the work resumes is a "
                             f"person's decision now, and the receipts under "
                             f"{root} are what they read",
                             **common))
            continue

        key = action_key(str(record.get("work_item")), record.get("epoch"), attempt)
        try:
            text = briefing(record, wait, verdict, grant_ref=grant_ref,
                            spend=spend, attempt=attempt, key=key)
        except WakeRefused as exc:
            # One record's briefing over the bound must not abort the plan for the
            # other records, and it must not be truncated into one that fits: the
            # instruction block is at the end, and a Parent started on a briefing
            # nobody read to the end is worse than one that is not started.
            rows.append(_row(record, wait, "REFUSE", "BRIEFING_OVER_BOUND",
                             str(exc), action_key=key, attempt=attempt, **common))
            continue
        rows.append(_row(record, wait, "WAKE", "READY",
                         f"{wait['says']}, {grant_ref} authorizes waking it, and "
                         f"attempt {attempt} of {spend['attempts_allowed']} is "
                         f"unspent",
                         action_key=key, attempt=attempt, briefing=text,
                         **common))

    counts: dict = {}
    for row in rows:
        counts[row["reason"]] = counts.get(row["reason"], 0) + 1
    return {"schema": SCHEMA, "now": now, "receipts": str(root),
            "equipment": equipment(unit_dir), "item": item,
            "rows": rows, "counts": counts,
            "would_wake": [row["checkpoint_ref"] for row in rows
                           if row["action"] == "WAKE"]}


def _budget_sentence(spend: dict, grant_ref: str) -> str:
    if spend["reason"] == "NO_ATTEMPT_BUDGET":
        return (f"{grant_ref} authorizes waking this Item and carries no positive "
                f"`max_attempts`, so this routine cannot say how many times it may "
                f"wake it. An unbounded receiver is one whose number of Parents "
                f"nobody chose")
    if spend["reason"] == "NO_WALL_BUDGET":
        return (f"{grant_ref} authorizes waking this Item and carries no positive "
                f"`wall_seconds`, so this routine cannot say how long the Parent it "
                f"starts may run — and `invoke_once` kills what outlives its bound, "
                f"so starting one without a bound is starting one to kill it")
    return (f"{grant_ref} carries `budget.exhausted`, so the delegation it derives "
            f"from has spent the budget it was given. A wake under it would be "
            f"authority the grant no longer has")


# ---------------------------------------------------------------------------
# What the Parent is told
# ---------------------------------------------------------------------------


def briefing(record: dict, wait: dict, verdict: dict, *, grant_ref: str,
             spend: dict, attempt: int, key: str) -> str:
    """The bounded document handed to the Parent this routine starts.

    It is written to be READ BY A PARENT AND NOT TO DECIDE FOR IT. Every line is a
    field of the checkpoint, of the wait, or of the grant — the four things this
    routine read — and the instruction block at the end is the one
    `docs/context-capsule.md` and the accepted design both name: reconcile the
    revision, the grant, the owner, the attempts, the unresolved effects and the
    write scope BEFORE acting, and receive a child's result exactly once.

    The context ref is here and is the reason this is not a forgery. A delegate
    that wakes work is legitimate only while the delegation it derives from still
    authorizes — `context/delegation.py`'s sentence — and the receiver has just
    checked that. Naming the ref is what lets the Parent check it again for
    itself rather than take this document's word for it.

    Over `MAX_BRIEFING` is a refusal and not a truncation: a truncated briefing is
    one whose last line — the instruction — is the line that goes missing, and a
    Parent that acted without it would have been started by a document nobody
    read to the end.
    """
    grant = verdict["envelopes"][grant_ref]
    waiting = record.get("waiting_on") or {}
    if waiting.get("kind") == "child":
        waited_on = ", ".join(waiting.get("refs") or []) or "(none named)"
    else:
        waited_on = f"until {waiting.get('until')}"
    lines = [
        "A ServerOS checkpoint says this Item can be resumed, and this process",
        "was started by the node's continuation routine rather than by a person.",
        "",
        f"  work item            {record.get('work_item')}",
        f"  epoch                {record.get('epoch')}  (a later epoch would have "
        f"taken this scope over; this one still owns the Item)",
        f"  checkpoint           {record.get('checkpoint_ref')}  written "
        f"{record.get('written_at')}",
        f"  held by              {record.get('parent_owner')}  (a Parent that "
        f"exited; the Item is the scope and the epoch is only how it changed hands)",
        f"  requirement revision {record.get('requirement_revision') or '(not named by this record)'}",
        f"  waited on            {waiting.get('kind')} {waited_on}",
        f"  and now              {wait.get('says')}",
        "",
        f"  next safe action     {record.get('next_safe_action')}",
        f"  remaining results    "
        + (", ".join(record.get('remaining_results') or []) or "(none outstanding)"),
        f"  accepted evidence    "
        + (", ".join(record.get('evidence_locators') or [])
           or "(this record carries none)"),
        f"  child attempts       "
        + (", ".join(record.get('child_attempts') or []) or "(none named)"),
        f"  unresolved effects   "
        + (", ".join(record.get('unresolved_effects') or []) or "(none named)"),
        f"  write scope          "
        + (", ".join(record.get('write_scope') or []) or "(unnamed)"),
        "",
        f"  authorizing grant    {grant_ref}",
        f"  under delegation     {grant.get('delegated_by')}",
        f"  grant covers items   {', '.join(str(i) for i in (grant.get('items') or []))}",
        f"  grant actions        {', '.join(str(a) for a in (grant.get('actions') or []))}",
        f"  grant expires        {grant.get('expires_at') or '(no window)'}",
        f"  this wake spends     attempt {attempt} of {spend['attempts_allowed']}, "
        f"up to {spend['wall_seconds']}s",
        f"  wake receipt key     {key}  (one key is one receipt: a second wake "
        f"under it replays rather than starts)",
        "",
        "BEFORE YOU ACT, RECONCILE — the receiver checked four things and this",
        "document is not a substitute for checking them again:",
        "",
        "  1. read the whole checkpoint:   serverosctl work continuations --item "
        + str(record.get('work_item')),
        "  2. re-check the current authority: does the delegation above still",
        "     authorize, and does the grant still cover this Item, its actions and",
        "     its scope? If it does not, stop — do not act on this document.",
        "  3. re-check the Requirement revision above against the current one. If",
        "     it has moved, this record was written under a Requirement that no",
        "     longer holds and continuing from it is continuing the wrong work.",
        "  4. look up every child attempt named above and receive each result",
        "     EXACTLY ONCE (`serverosctl work accept <cycle>`); check the",
        "     unresolved effects before taking any write, and act only inside the",
        "     write scope.",
        "",
        "Then continue the next safe action, or checkpoint again — including when",
        "the honest answer is that this Item is finished with for now, which is a",
        "disposition and not a silence, and this node has a verb for it:",
        "`serverosctl work park <checkpoint> --reason <why>` sets it down with the",
        "reason kept, and `work unpark` is the way back. Handing it to a person is a",
        "message and not a record.",
        "Do not treat this document as authority to widen the scope, to touch",
        "production, or to send anything externally.",
    ]
    text = "\n".join(lines) + "\n"
    if len(text) > MAX_BRIEFING:
        raise WakeRefused(
            f"the briefing for {record.get('checkpoint_ref')} is {len(text)} "
            f"characters and the bound is {MAX_BRIEFING}. A briefing over the "
            f"bound is not truncated here: the instruction block is at the end, "
            f"and a Parent that acted without having read it would have been "
            f"started by a document nobody finished")
    return text


# ---------------------------------------------------------------------------
# Starting one Parent, exactly once per attempt
# ---------------------------------------------------------------------------


def wake(rows, *, launcher, cwd, receipts, budget=None,
         max_output_bytes: int = 2_000_000) -> list:
    """Start a Parent for every READY row, and record what happened to each.

    One row's failure is not another row's: a lock another wake holds, a launcher
    that does not exist and a receipt that already exists are three answers about
    three keys, and this returns one entry per row so the caller can print them
    all. It raises nothing for a row it could not start — a timer that exited
    non-zero because one of four Items was in flight would be reporting a
    delivery failure about three deliveries that happened.

    TWO BOUNDS AND THE SMALLER ONE WINS. The grant's `wall_seconds` is how long
    the delegation says a cycle may take; `budget` is what the INSTALL says this
    routine may spend, and it is what the unit's `TimeoutStartSec=` is set to. A
    Parent that outlived either would be killed by `invoke_once` from inside and
    by systemd from outside, so the smaller bound is applied here and named in the
    result rather than left to whichever layer noticed first.

    `cwd` IS THE CALLER'S AND NOT THIS MODULE'S. A checkpoint names a write scope
    and not a checkout, so the working directory a Parent starts in is a decision
    the install makes (`--cwd`, or the unit's `WorkingDirectory=`), and a receiver
    that guessed one would be choosing where a Parent writes.
    """
    invocation = _invocation()
    results: list = []
    cwd = Path(cwd)
    receipts = Path(receipts)
    for row in rows:
        if row["action"] != "WAKE":
            continue
        timeout = row["wall_seconds"]
        if budget is not None:
            timeout = min(timeout, int(budget))
        outcome = {"checkpoint_ref": row["checkpoint_ref"],
                   "action_key": row["action_key"], "attempt": row["attempt"],
                   "launcher": list(launcher), "cwd": str(cwd),
                   "timeout": timeout}
        try:
            receipt = invocation.invoke_once(
                key=row["action_key"], argv=list(launcher), cwd=cwd,
                receipts=receipts, input_text=row["briefing"],
                timeout=timeout, readonly=False,
                max_output_bytes=max_output_bytes)
        except ValueError as exc:
            # Every `ValueError` in that helper is on the near side of `Popen`,
            # which is what makes «nothing was started» a fact here rather than a
            # hope. The two that reach a timer are the receipt lock another wake
            # still holds and a same-key-different-input clash; neither is
            # repaired by trying again at once, and the next tick is the retry.
            results.append({**outcome, "result": "NOT_STARTED",
                            "receipt_status": None,
                            "because": str(exc)})
            continue
        if receipt.get("replayed"):
            result, because = "REPLAYED", (
                "this action key already carried this exact input, so the receipt "
                "was returned and nothing was spawned now")
        elif receipt.get("status") == "REFUSED":
            result, because = "REFUSED", (
                f"`invoke_once` refused to start: {receipt.get('reason')}. Nothing "
                f"was spawned and no Parent is running under this key")
        elif receipt.get("executed_now"):
            result, because = "WOKEN", (
                f"a Parent was started and the receipt records "
                f"{receipt.get('status')} when it ended")
        else:
            result, because = "REFUSED", (
                f"the receipt records {receipt.get('status')} and claims no "
                f"execution, so nothing is known to have started")
        # The receipt is selected rather than copied whole: `output_sha256` and
        # the digest fields are `invoke_once`'s bookkeeping, and the file is the
        # evidence for a reader who wants them.
        results.append({**outcome, "result": result, "because": because,
                        "receipt_status": receipt.get("status"),
                        "receipt": receipt.get("action_key"),
                        "started_at": receipt.get("started_at"),
                        "finished_at": receipt.get("finished_at"),
                        "process_exit_code": receipt.get("process_exit_code"),
                        "effect": receipt.get("effect")})
    return results


def wake_now(control, *, now: str, launcher, cwd, item=None, unit_dir=None,
             receipts=None, budget=None, max_output_bytes: int = 2_000_000) -> dict:
    """Plan, then start a Parent for each READY row. The timer's whole body.

    One call and one ordering, so that the decision a person reads from
    `--dry-run` and the decision the timer acts on are the same code and not two
    compositions of it.
    """
    planned = plan(control, now=now, item=item, unit_dir=unit_dir, receipts=receipts)
    planned["launcher"] = list(launcher)
    planned["cwd"] = str(cwd)
    planned["budget"] = None if budget is None else int(budget)
    if not launcher:
        planned["results"] = []
        planned["because"] = (
            "no Parent command line was given, so nothing could be started. "
            "This is `work wake` without `--parent`: the plan above is what this "
            "node would wake, and the checkpoints on it stay exactly as they are")
        return planned
    planned["results"] = wake(planned["rows"], launcher=launcher, cwd=cwd,
                              receipts=planned["receipts"], budget=budget,
                              max_output_bytes=max_output_bytes)
    planned["because"] = None
    return planned
