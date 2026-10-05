#!/usr/bin/env python3
"""§21 — an anomaly that persists is ONE alert, and it says when it ends.

WHAT THIS IS
------------
`runtime/anomaly.py` and `runtime/resource_anomaly.py` answer "is something
wrong right now". `runtime/notifications.py` answers "what is the sentence for
this, and can the transport carry it". What neither of them has is the thing
between those two questions, and this module is only that: an anomaly with a
memory.

Two product questions, and no others:

  1. after ServerOS has already detected an anomaly, can it tell a person?
  2. when the same anomaly persists, does it avoid saying so every minute, and
     does it speak once more when the anomaly ends?

Everything here is in service of those two. There is no metric history, no
rate, no window query, no aggregation, no queue, no retry loop, no scheduler
and no trend. An alert is a four-valued state attached to a stable identity
plus the record of what was measured when — and the reason that is enough is
that the detector already recomputes the measurement on every tick. Storing it
here as well would be a second copy of a fact with its own idea of when it was
taken.

WHAT IT IS NOT ALLOWED TO DO
----------------------------
Nothing in this file stops, restarts, kills, prunes or reclaims anything. There
is no call to a signal, a socket, a subprocess or the Docker API, and the only
verb with an effect is `deliver`, which sends a sentence. §15 states the rule as
a permission rather than a prohibition — the strongest consequence an alert may
carry is a RECOMMENDATION — and the wording matters: `alert != authorization`.
An alert is a finding a person is told about; it is not a mandate, and there is
no field here that a later reader could mistake for one.

THE ONE DATA FLOW (§2)
----------------------
    producer → semantic event → this module's policy → notifications → provider

A producer (the anomaly detectors) writes "what the fact is". The provider owns
"how it reaches a person". This module sits between them and owns the third
thing, which is *when* — and it owns nothing else. It does not probe, it does
not render a provider-specific payload, and it never speaks to a transport
itself: `notify_for()` takes a provider as an ARGUMENT for exactly that reason,
so nothing here can import one and no import graph can grow a daemon that is a
client of a messaging system.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

CONTRACT = _HERE / "notification-contract.yaml"
SCHEMA = "serveros/alerts.v1"

# The two events this module raises, named here so that the contract's
# `raised_by: runtime/alerting.py#notify_for` reference has a literal to find.
# They are the only event names in this file, and the gate that checks the
# producer table depends on that staying true.
OPENED = "alert.opened"
RESOLVED = "alert.resolved"

# What a subject prefix says about whether it names a managed resource. Used by
# `resource_ref()` and nothing else.
RESOURCE_PREFIXES = ("filesystem:", "runtime-group:", "container:",
                     "runtime:", "tunnel:")


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _anomaly():
    return _load("serveros_runtime_anomaly", _HERE / "anomaly.py")


def _graph():
    return _load("serveros_runtime_graph", _HERE / "graph.py")


def _notifications():
    return _load("serveros_runtime_notifications", _HERE / "notifications.py")


def _store_io():
    # The atomic write lives with the other durable control-plane store rather
    # than being written a second time here. Two implementations of "write a
    # JSON file without a torn read" is two chances for one of them to be
    # wrong, and the one that is wrong is the one nobody is looking at.
    return _load("serveros_runtime_agent_control_store",
                 _HERE / "agent_control_store.py")


class AlertError(RuntimeError):
    pass


def contract() -> dict:
    import yaml
    data = yaml.safe_load(CONTRACT.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise AlertError(f"{CONTRACT} must contain a mapping")
    return data


def policy() -> dict:
    """The `alerts:` block. Every number this module compares against."""
    block = contract().get("alerts")
    if not isinstance(block, dict):
        raise AlertError(
            f"{CONTRACT} declares no `alerts:` policy. This module has no "
            f"threshold of its own to fall back to: a cooldown written next to "
            f"the comparison that uses it is a number nobody reviews")
    return block


def detectors() -> dict:
    """name → the module that answers to it, in declared order.

    The keys are what a row stores in its `detector` field and what
    `evaluate(detector=)` must be called with; the values are documentation of
    which module that is TODAY. Nothing loads the values — `runtime/view.py` is
    the only loader — so a rename is a fact this mapping records rather than a
    path anything follows.

    It is a mapping and not a list because the pairing of name to detector is
    the whole subject: reading them positionally is how the wrong document ends
    up under the right name.
    """
    declared = policy().get("detectors")
    if not isinstance(declared, dict) or not declared:
        raise AlertError(
            f"{CONTRACT} declares no `alerts.detectors`. An alert row stores "
            f"the name of the detector that opened it, and only that detector "
            f"may close it — with no names declared, every pass would be a "
            f"stranger to every row")
    return dict(declared)


def states() -> tuple[str, ...]:
    return tuple(policy()["states"])


def severity_words() -> dict:
    """low/medium/high → the word a person reads, in declared order."""
    return dict(policy()["severity_words"])


def _ranks() -> dict:
    return {word: index for index, word in enumerate(severity_words())}


# ---------------------------------------------------------------------------
# Identity. §7, and the whole of §7.
# ---------------------------------------------------------------------------


def identity(*, code: str, device_ref: str, subject_ref: str) -> str:
    """What makes two sightings the same alert.

    `code + device + subject`. Not the pid: a runtime that a supervisor kills
    and relaunches is the same alert, and keying on pid is how a restart loop
    produces one message per restart. Not the container name: names are reused,
    and two deployments of one name are two incidents that must not merge. Not
    a timestamp: an identity that includes when it happened changes every time
    it happens, which is the same as having no identity at all.

    The subject is the third part because the first two are not enough — one
    device can hold two full filesystems, and they are two alerts.
    """
    return f"{code}|{device_ref}|{subject_ref}"


def alert_ref(identity_str: str) -> str:
    """A stable, greppable handle for an identity. Never a sequence number.

    A hash rather than a counter because a counter is assigned per process, and
    this store outlives the process that wrote it: a daemon restart would
    renumber every alert, and "alert 3" would mean one thing before the restart
    and another after. Truncated to 16 hex characters because this travels in
    a sentence a person reads out loud.
    """
    digest = hashlib.sha256(identity_str.encode("utf-8")).hexdigest()[:16]
    return f"alert:{digest}"


def resource_ref(subject_ref: str) -> str | None:
    """The managed resource an anomaly is about, when the subject names one.

    Derived rather than supplied by the producer. A `resource_ref` field that
    each producer fills in is a field some producers fill in — and "this alert
    has no resource" and "nobody wrote the resource for this code" would then
    be the same JSON. `host:memory` is a measurement subject, not a resource,
    and a host-wide pressure belongs to no single one; `filesystem:/` and
    `runtime-group:…` do name one.
    """
    return subject_ref if subject_ref.startswith(RESOURCE_PREFIXES) else None


# ---------------------------------------------------------------------------
# The store. Two documents, two atomic writes, refused rather than emptied.
# ---------------------------------------------------------------------------
#
# HOT AND COLD, AND WHY THIS IS NOT RETENTION
# -------------------------------------------
# The register keeps one row per identity, and an identity that has ever opened
# keeps its row so that a recurrence is legible AS a recurrence. That has no
# expiry and this change adds none: nothing below is dropped for being old.
#
# What it was doing was paying for history on every pass. Measured on this node
# 2026-10-04: 13,189 rows and 27.6 MB, of which 11,833 rows and 90.7% of the
# bytes are RESOLVED — the record of what is over. An evaluation pass rewrites
# the document whole, and a pass runs every minute, so two rewrites per tick
# moved 20.7 MB of closed episodes from one side of a rename to the other
# unchanged, at 0.3364 s each inside a 5.0 s tick.
#
# So the document is split by ROLE and not by AGE:
#
#   state.json   the HOT set — the rows a pass may open, hold, escalate,
#                snooze, acknowledge or resolve. Written every pass.
#                2.58 MB, 0.0335 s.
#   ledger.json  the COLD ledger — the whole RESOLVED episode, authoritative
#                and complete. Written only when a resolution, a re-resolution
#                or a recovery announcement actually changes it. 25.07 MB,
#                0.2917 s, and now paid on the events that cause it rather
#                than on the clock.
#
# `alerts()` still returns the UNION of the two, so every reader that existed
# before this split — `listing`, `evaluate`, `deltas`, `ensure_baseline`,
# `record`, `alert_model` — sees exactly the document it saw before. That
# equivalence is why the union is built at LOAD and not at read: no caller has
# to know there are two files.
#
# WHICH FILE WINS WHERE THEY OVERLAP, AND WHY IT IS THE HOT ONE
# -------------------------------------------------------------
# The two writes of one commit are not atomic with each other, so an
# interruption can leave a ref in both files. The rule is that the hot row wins,
# and it is chosen because both residues it can produce are self-healing:
#
#   * A row resolved, the ledger was written, and the hot file still holds the
#     older OPEN copy (the hot write is last). Hot wins, the row reads OPEN,
#     and the next pass sees an identity that is no longer firing, resolves it
#     again and sends the recovery message one tick late. Correct, delayed.
#   * A row re-opened, the ledger's copy is the older RESOLVED episode, and the
#     hot file has the live row. Hot wins and the reopening is not lost.
#
# The reverse rule would turn the first case into a row that reads RESOLVED
# while it is still firing, which is a person not being told.
#
# THE LEDGER ONLY EVER GAINS
# --------------------------
# A row that leaves the hot set on re-opening is NOT removed from the ledger.
# It resolved; that is history, and it is `evaluate` re-opening the identity
# that makes it live again. Removing it would make the ledger a second opinion
# about the present, which is the one thing two files could go wrong about.
# Nothing reads the ledger directly — `alerts()` is the only door — so a
# superseded episode sitting in it is invisible to every caller, and a history
# query goes through the same union as everything else.
#
# WHAT IS NOT SPLIT: the delivery cursor (`delivery.json`) and the card receipt
# ledger (`cards/delivery.json`) are siblings of the register and are untouched.
# Both name `state.json` only as the file whose directory they live in.


LEDGER_SCHEMA = "serveros/alerts-ledger.v1"


def ledger_path(alert_state_path) -> Path:
    """Where the cold ledger lives, given where the hot register lives.

    A SIBLING, by the same argument `alert_digest.cursor_path` gives for the
    delivery cursor: two documents with two lifetimes do not share one file,
    because the one rewritten every minute would then also be the one whose
    torn write loses the other.
    """
    return Path(alert_state_path).with_name("ledger.json")


class AlertStore:
    """The alert state at rest. Not a historian: one row per live identity.

    A RESOLVED alert stays as a row, because "this was open and is now closed"
    is what makes the next occurrence legible — a store that dropped it would
    make every recurrence look like the first one, and the first one notifies
    immediately by design. Since 2026-10-04 that row lives in the cold ledger
    rather than in the hot file; see the block comment above for why the
    difference is invisible to every caller. `alerts()` is the only door.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.ledger_path = ledger_path(self.path)
        # Loaded, not written. A register that predates the split still holds
        # its RESOLVED rows in `state.json`, so the union below is complete
        # either way and the first `commit` performs the split as an ordinary
        # atomic write. There is no separate migration step and no window in
        # which the rows exist in neither file.
        self._ledger: dict = self._read(self.ledger_path)["alerts"]
        self._data = {"schema": SCHEMA,
                      "alerts": {**self._ledger, **self._read(self.path)["alerts"]}}

    def _read(self, path: Path) -> dict:
        if not path.exists():
            return {"alerts": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Refused, not replaced. An unreadable alert store means every open
            # alert is no longer known, and a process that started anyway would
            # re-notify all of them as new — which is the spam this module
            # exists to prevent, produced by the failure path.
            raise AlertError(
                f"{path} exists and cannot be read ({type(exc).__name__}: "
                f"{exc}). Refusing to start with an empty alert store: every "
                f"open alert would notify again as if it were new.") from exc
        if not isinstance(data, dict):
            raise AlertError(f"{path} is not a JSON object")
        return {"alerts": data.get("alerts") or {}}

    def alerts(self) -> dict:
        """A copy. Callers mutate the copy and hand it back via `commit`."""
        return json.loads(json.dumps(self._data["alerts"]))

    def get(self, ref: str) -> dict | None:
        return self.alerts().get(str(ref))

    def commit(self, alerts: dict) -> None:
        """Two documents, each in one atomic write, split by role.

        Whole-document rather than per-record within each file, because one
        evaluation pass opens, updates, resolves and holds in the same tick: a
        store written row by row would have a window in which an alert is open
        in one row and still pending in another, and a crash inside it would
        leave a state no code path produces.

        The ledger is written FIRST and the hot file second, so the hot write
        is the commit point: an interruption before it leaves a ref in both
        files rather than in neither, and the overlap rule in the block comment
        above resolves that to the answer that is merely late instead of the
        one that is wrong. The ledger is skipped entirely when nothing about it
        changed, which is the whole of the saving on a routine pass.

        Named `commit` and not `replace`, which is what it was. `replace` takes
        one positional argument, which is exactly the arity of
        `Path.replace(target)`, so `store.replace(alerts)` and
        `tmp.replace(destination)` are the same call to any reader — human or
        machine. `mutation_guard` said so: it reported these four call sites as
        filesystem mutations, and it was right that it could not tell. The
        contract's word for this half of the write-temp-then-rename idiom is
        already "commit" (see `actuator-contract.yaml#atomic-write-commit`), so
        the fix was to use the name the tree already had rather than to declare
        a `.replace()` on a store object as a path operation, which would have
        been a contract entry describing something that is not true.
        """
        hot: dict = {}
        cold: dict = {}
        for ref, row in alerts.items():
            (cold if row.get("state") == "RESOLVED" else hot)[ref] = row

        # A plain `!=` over the resolved rows, and not a fingerprint: measured
        # on this node at 0.0157 s for all 11,833 of them, against 0.0671 s for
        # a tuple built by hand — the comparison the tree already has is both
        # cheaper and impossible to get subtly wrong by leaving a mutable field
        # out of it.
        if any(self._ledger.get(ref) != row for ref, row in cold.items()):
            self._ledger = {**self._ledger, **cold}
            _store_io()._atomic_write(
                self.ledger_path,
                {"schema": LEDGER_SCHEMA, "alerts": self._ledger})

        # The union again, and not simply `alerts`: that is what the two files
        # now hold, and a running store whose view differs from the view its own
        # restart would build is a store that changes behaviour when it is
        # restarted, which is the worst property a durable one can have.
        self._data["alerts"] = {**self._ledger, **alerts}
        _store_io()._atomic_write(self.path,
                                  {"schema": SCHEMA, "alerts": hot})

    def summary(self) -> dict:
        rows = self.alerts()
        by_state: dict[str, int] = {s: 0 for s in states()}
        for row in rows.values():
            state = str(row.get("state"))
            by_state[state] = by_state.get(state, 0) + 1
        # `total` and `by_state` still describe the union, so nothing that read
        # this before reads a different number. The two extra keys say where
        # the rows are, which is the one thing the union hides.
        return {"schema": SCHEMA, "total": len(rows), "by_state": by_state,
                "path": str(self.path), "ledger_path": str(self.ledger_path),
                "ledger_rows": len(self._ledger)}


# ---------------------------------------------------------------------------
# Time. Two functions, and both refuse to invent a number.
# ---------------------------------------------------------------------------


def _seconds_between(then, now) -> float | None:
    return _graph().seconds_between(then, now)


def _shift(stamp, seconds: int) -> str | None:
    """`stamp` moved forward, in the same ISO form, or None if it will not read.

    None rather than `stamp` unchanged: a snooze that could not be given an
    expiry is not a snooze that expires now, and the caller is told which.
    """
    import datetime as _dt
    if not isinstance(stamp, str):
        return None
    text = stamp.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    moved = moment + _dt.timedelta(seconds=seconds)
    return moved.isoformat().replace("+00:00", "Z")


def _duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    hours = seconds / 3600
    return f"{hours:.1f} h"


# ---------------------------------------------------------------------------
# The decision. Pure: a signal, the policy, and what is already known.
# ---------------------------------------------------------------------------


def _pending(kind: str, *, at: str) -> dict:
    """What still has to be said, and the earliest moment it may be said.

    `not_before` is here rather than recomputed at delivery time because §12
    requires that a FAILED delivery not become a retry loop. The clock starts
    when the attempt is made; a delivery that did not arrive leaves the intent
    pending, and the next surface that runs sees the earliest moment it may try
    again rather than a queue that drains as fast as it can be pumped.
    """
    return {"kind": kind, "not_before": at}


def _presentation():
    """`presentation`, loaded at CALL time and never at import time.

    `presentation.py` loads THIS module from its own top level, so a load here
    at module scope would be a cycle. The same reason, the same loader and the
    same shape as `alert_digest._presentation`, which reads the attention table
    through the same door.
    """
    return _load("serveros_runtime_presentation", _HERE / "presentation.py")


def _sayable_codes() -> frozenset:
    """Which codes may occupy a line on a person's card, from the contract.

    Asked of `presentation` and flattened nowhere else. The contract says NO
    OTHER FILE MAY ASSIGN AN ATTENTION LEVEL, and `alert_digest.face_table`
    already made this exact choice for this exact question; a third flattening
    written here would be a third authority over one axis.

    `criticality` is not an input, and that is not an omission:
    `presentation.attention_of` can only move a `declared` code from
    `escalation.from` to `escalation.to`, and the contract puts both of those on
    the face. Escalation cannot move a code across THIS line, so asking it here
    would add an input that cannot change the answer.

    Read once per pass and not once per row: `face_table`'s docstring records
    what asking per row cost this node (24.7 s inside a call whose client waits
    5.0 s).
    """
    presentation = _presentation()
    face = tuple(presentation.attention_policy().get("on_the_face") or ())
    return frozenset(
        code for code, entry in presentation.code_expectations().items()
        if str(entry.get("level") or "") in face)


def _may_be_said(code: str, *, sayable) -> str | None:
    """Why nothing about `code` can be delivered, or None if something can.

    The only surface this node has for telling a person about a finding is a
    card line, and `alerts.attention.on_the_face` decides which codes may occupy
    one. A code outside that set has nowhere to be delivered TO, so an intent to
    say it is a debt with no payee — and a register that accumulates those stops
    being a record of what is owed, which is the only thing `pending` is for.

    Measured on this node 2026-10-04, before this rule: 13,207 rows carried a
    `pending` and 1,204 had ever been announced, and 1,193 of those
    announcements were for codes this table has never put on a card. The Status
    Card's own 「未送达的历史意图（N）」 line was reading that difference as N
    messages that failed to arrive.

    The finding is not dropped and the row is not made quiet: `pending` is a
    claim about what will be SAID, and withdrawing one changes nothing about
    what is known. The row stays in the register, stays graded, and stays in the
    digest's own buckets.
    """
    if str(code) in sayable:
        return None
    return (f"{code} is graded and nothing about it can be delivered: its "
            f"attention level is not on a card's face, so no message about it "
            f"has a surface to arrive on")


def _opening_intent(code: str, *, at: str, sayable) -> tuple[dict | None, str | None]:
    """(the intent a signal that just fired owes, or None with the reason).

    §12 asks for a message when something becomes wrong; this is where §12 meets
    §2, which decides what may be printed.
    """
    why = _may_be_said(code, sayable=sayable)
    if why:
        return None, why
    return _pending("opened", at=at), None


def _resolving_intent(alert: dict, *, at: str,
                      sayable) -> tuple[dict | None, str | None]:
    """(the recovery intent a closing alert owes, or None with the reason).

    §10: a person who was told about a problem is told it ended. Both halves are
    required, and each is a different way of owing nothing. The code has to be
    one a card may carry, or the recovery has nowhere to arrive — a recovery
    message is a message. And somebody has to have been told, because a recovery
    message takes back an announcement: an episode nobody heard about has none
    to take back.

    The second half is why `last_notified_at` and not `state`. 11,077 rows on
    this node were RESOLVED and carried a `resolved` intent for a first message
    that had never been sent, so what the register was calling a recovery was in
    several thousand cases the first word anybody would have had on the subject.
    """
    why = _may_be_said(str(alert.get("code") or ""), sayable=sayable)
    if why:
        return None, why
    if alert.get("last_notified_at") is None:
        # An attempt that failed is named here rather than left to be
        # discovered: this is the one case where an intent is dropped AND a
        # delivery was tried, and the sentence is the only place the two facts
        # meet. `delivery_failure` stays on the row for `alert inspect`.
        tried = alert.get("delivery_failure")
        return None, (f"{alert.get('code')} closed without ever having been "
                      f"announced, and a recovery message takes back an "
                      f"announcement — there is none to take back"
                      + (f"; the last attempt answered {tried!r}" if tried else ""))
    return _pending("resolved", at=at), None


def _withdrawn_intent(alert: dict, *, at: str, sayable) -> str | None:
    """True up a row that carries an intent these rules would not write.

    The two rules are applied at the two moments a row OPENs and CLOSEs, and
    this is the third moment: every pass, to the rows it already owns. It is not
    optional housekeeping. This node has 13,207 rows written by the rules as
    they stood, and a rule governing only new rows would leave that number on
    the books forever — the same untrue claim with a longer deadline, printed on
    the Status Card the whole time.

    Returns the reason the claim was withdrawn, or None when the row was already
    honest. `state` is deliberately not consulted: an intent is a claim about
    what will be SAID, so whether the underlying finding is open decides which
    rule applies, never whether the claim is payable.
    """
    kind = (alert.get("pending") or {}).get("kind")
    if kind == "opened":
        _, why = _opening_intent(str(alert.get("code") or ""), at=at,
                                 sayable=sayable)
    elif kind == "resolved":
        _, why = _resolving_intent(alert, at=at, sayable=sayable)
    else:
        return None
    if why is None:
        return None
    alert["pending"] = None
    return (f"a `{kind}` intent was withdrawn from this row, which was written "
            f"by the rules as they stood before the attention table governed "
            f"`pending`: {why}")


def _should_repeat(alert: dict, *, severity: str, at: str,
                   cooldowns: dict, ranks: dict) -> tuple[bool, str | None]:
    """Whether a PERSISTING anomaly may speak again, and why not if not.

    The clock runs from `last_attempt_at` rather than from `last_notified_at`,
    and that is a deliberate reading of §12. If it ran from the last SUCCESSFUL
    delivery, a transport that is down would leave `last_notified_at` untouched
    forever, the cooldown would never elapse, and the alert would go silent for
    the duration of the outage — the one period in which a person most needs to
    know. If it ran from nothing at all, every tick would try again, which is
    the retry loop §12 forbids. The attempt is the honest clock: it is the last
    time this node did something about telling somebody.
    """
    since = alert.get("last_attempt_at") or alert.get("first_seen")
    elapsed = _seconds_between(since, at)
    if elapsed is None:
        return False, (f"the elapsed time since {since!r} could not be computed; "
                       f"an unreadable clock is not a reason to speak")
    cooldown = cooldowns.get(severity)
    if cooldown is None:
        return False, f"no cooldown is declared for severity {severity!r}"
    if elapsed < float(cooldown):
        return False, (f"in cooldown: {elapsed:.0f}s since the last attempt, "
                       f"{cooldown}s declared for {severity}")
    told = alert.get("last_notified_severity")
    if told is not None and ranks.get(severity, -1) < ranks.get(told, -1):
        # A downgrade is not news. The alert stays OPEN and stays visible to
        # anyone who looks; what it does not do is spend a message saying that
        # the thing it already told them about got less bad. Its recovery
        # message still arrives, which is the message that matters.
        return False, (f"the severity fell from {told} to {severity}; a "
                       f"downgrade is not news")
    return True, None


def _graded_measurement(alert: dict, *, levels: dict, ungraded: dict,
                        subjects: set | None = None) -> tuple:
    """(verdict, why, kind) for an alert whose signal did not fire this tick.

    RESOLVED, or HELD with the reason — never a guess. The four answers this
    distinguishes are the four that a single "the signal is gone" would
    collapse:

      * the code was not evaluated at all → HELD. "Nobody looked" is not
        "the disk is fine", and this is the same distinction
        `codes_graded` vs `codes_not_evaluated` draws one level down.
      * the subject is a measurement and it came back below its floor →
        RESOLVED.
      * the subject is a measurement and it did NOT come back below its floor,
        or was reported and came back unreadable → HELD. Without this the alert
        would flap: at 84.9% the signal is absent and at 85.1% it is present,
        and a store that read absence as recovery would open and close the same
        alert on either side of one threshold.
      * the subject is NOT IN THE PASS'S INVENTORY at all → RESOLVED, kind
        `subject_gone`. This is the fourth answer, and it was missing for two
        months. A runtime group's ref embeds a pid, so its ref is a LIFETIME:
        when the group ends its ref never recurs. Held under the third answer,
        such an alert said "a subject that stopped being measured has not
        recovered" forever — measured on 2026-10-04, 1,088 alerts were frozen
        OPEN saying exactly that, and **all 1,088 subjects no longer existed**.
        The sentence was not cautious, it was false.

    `subjects` is the pass's inventory of what it enumerated, from the
    detection document. It is OPTIONAL and the absence of the key is not the
    same as an empty one: a document that carries no inventory is evaluated
    exactly as it was before this existed, so every hand-built test document
    and every older document is unaffected. `evaluate()` turns an empty list
    into `None` for that reason — a pass that enumerated nothing has not
    established that everything is gone.

    The order of the branches is the safety property, not an accident. The
    `not_evaluated` test runs FIRST, so a pass whose process table failed to
    answer holds every group alert before the inventory is ever consulted: a
    degraded pass cannot mass-resolve. That is the same guard that keeps
    `INPUT_CODES` honest one level up, and it is why `subject_gone` can be
    trusted to mean "gone" rather than "not looked at today".

    `kind` is the machine-readable half of `why`, and it is a separate return
    value rather than something a consumer decodes from the sentence. Two of
    these verdicts are opposite facts wearing similar words —
    `not_evaluated` means nobody looked and `above_floor` means somebody looked
    and the value is still up — and a card that had to tell them apart by
    matching prose would be a card that changed meaning when somebody fixed a
    typo. `hold_kind` is stored on the alert; `hold_reason` stays for a person.
    """
    code, subject = alert["code"], alert["subject_ref"]
    if code in ungraded:
        return "HELD", (f"{code} was not evaluated this tick "
                        f"({ungraded[code]}); an unmeasured code resolves nothing"), \
               "not_evaluated"
    margin_key = alert.get("margin_key")
    if margin_key is None:
        # Not a measurement. A declaration either holds or it does not, and
        # there is no distance for it to come back. `RUNTIME_UNOWNED` stopping
        # being emitted IS the fact that it was fixed.
        return "RESOLVED", (f"{code} is not a measurement — no recovery margin "
                            f"is declared for it — so it closes when it stops "
                            f"being reported"), "not_a_measurement"
    row = levels.get(f"{code}|{subject}")
    if row is None:
        if subjects is not None and subject not in subjects:
            return "RESOLVED", (f"{subject} was not in this pass's inventory "
                                f"of what it looked at, so {code} is about "
                                f"something that is no longer there — that is "
                                f"an ending and not a gap in the reading"), \
                   "subject_gone"
        return "HELD", (f"the measurement for {subject} was not reported this "
                        f"tick; a subject that stopped being measured has not "
                        f"recovered"), "unmeasured"
    thresholds = _thresholds()
    margin = thresholds.get(margin_key)
    if margin is None:
        return "HELD", (f"{margin_key} is not declared, so there is no distance "
                        f"to come back and no comparison to make"), \
               "margin_undeclared"
    value, threshold = row.get("value"), row.get("threshold")
    if value is None or threshold is None:
        return "HELD", \
               f"{code} was reported without a usable value or threshold", \
               "unusable_measurement"
    floor = threshold - float(margin)
    if float(value) <= floor:
        return "RESOLVED", (f"the measurement came back to {value} "
                            f"(≤ {threshold} − {margin})"), "below_floor"
    return "HELD", (f"still {value}, above the {floor} it has to come back "
                    f"below before this closes"), "above_floor"


_THRESHOLD_CACHE: dict = {}


def _thresholds() -> dict:
    """`attribution-contract.yaml#thresholds`, read once per process.

    Read once and cached because an evaluation pass asks per alert, and the
    file does not change during one. A rewrite to make this hot-swappable would
    be a policy-reload feature, which is not what §9 asked for: §9 asked that
    the NUMBERS live in policy, and they do.
    """
    if not _THRESHOLD_CACHE:
        _THRESHOLD_CACHE.update(_anomaly().load_thresholds())
    return _THRESHOLD_CACHE


_THRESHOLD_KEY_CACHE: dict = {}


def threshold_keys() -> dict:
    """code → the KEY of the threshold it is graded against.

    Two tables, because this node has two detectors and the contract spells the
    fact in both of their vocabularies: `attribution-contract.yaml` names a key
    per resource code (`threshold:` on each `resource_anomalies:` entry, which
    `test_resource_anomaly.py` already asserts against the detector's own
    table), and `anomaly.THRESHOLD_KEYS` names it for the runtime codes, whose
    contract entries carry no `threshold` field at all.

    Read, not derived, and deliberately NOT read from a detector's private
    table. The row a detector hands over stores `margin_key`, which is derived
    FROM this key and no longer carries its direction — `memory_rss_high_bytes`
    becomes `memory_rss_recovery_margin`, and the `_high` is consumed on the
    way. A renderer that inverted a margin key to recover a direction would be
    guessing at a derivation that already happened one layer down.
    """
    if not _THRESHOLD_KEY_CACHE:
        out: dict = {}
        try:
            import yaml
            path = getattr(_anomaly(), "CONTRACT", None)
            document = (yaml.safe_load(Path(path).read_text(encoding="utf-8"))
                        if path else {}) or {}
        except (OSError, Exception):  # noqa: BLE001 - unreadable contract
            document = {}
        for entry in document.get("resource_anomalies") or ():
            if isinstance(entry, dict) and entry.get("code") and entry.get("threshold"):
                out[str(entry["code"])] = str(entry["threshold"])
        for code, key in (getattr(_anomaly(), "THRESHOLD_KEYS", None) or {}).items():
            out.setdefault(str(code), key)
        _THRESHOLD_KEY_CACHE.update(out)
    return _THRESHOLD_KEY_CACHE


def direction(threshold_key: str | None) -> str:
    """The comparison a threshold key declares, read off its own suffix.

    A row stores the threshold's VALUE and not what it is compared with, so
    without this a face can print `97.3%（阈值 90.0%）` — a number, a
    parenthesis and no statement of which side of the line it is on. §4 asks
    for value, operator and threshold together, and the operator is not a free
    choice to be made per code at the renderer: it is the shape of the key the
    detector already grades against.

    `_high` and `_high_bytes` are one end (≥), `_critical` names the second tier
    of the same quantity and is an upper bound as well. A key of neither shape
    prints NO operator rather than a guess — `cpu_sustained_seconds`,
    `error_window_seconds` and the like are windows, not lines to cross, and a
    face that stated a comparison nobody declared would be making the class of
    claim this whole module exists to keep out. `_low` is accepted because the
    suffix means what it says even though no code uses it today, so that the
    first lower-bound measurement added to the contract does not need this
    function changed to be read correctly.
    """
    key = str(threshold_key or "")
    if key.endswith("_high") or key.endswith("_high_bytes") \
            or key.endswith("_critical"):
        return "≥"
    if key.endswith("_low") or key.endswith("_low_bytes"):
        return "≤"
    return ""


def crosses(reading, threshold, arrow: str) -> bool:
    """Whether this reading is on the finding's side of its own threshold.

    The operator states a comparison, and a comparison printed beside two
    numbers without being performed is a claim. A register holds rows on BOTH
    sides of their line — a RESOLVED row is the same row after the reading came
    back down, and the PSI pair travels beside a swap watermark whether or not
    anything is stalling — so a face that stamped the operator on every
    threshold would print `0.0%（≥ 阈值 1.0%）`, asserting an inequality its own
    two numbers contradict. Which is the failure §4 names, arrived at from the
    other direction: not a missing operator, but one attached to a reading that
    does not satisfy it.

    So the operator appears only when it holds. When it does not, the threshold
    is printed and no comparison is, which is exactly the string this function
    rendered before it knew about direction — nothing is claimed, so nothing
    can be claimed wrongly. The strict `<` of the other side is deliberately
    NOT printed in its place: an alert does not close the moment its reading
    drops below the threshold, it closes `margin_key` below it, so `below the
    line` is not the same fact as `not currently a finding` and this renderer
    does not own the state that decides it.

    An incomparable pair takes the same path, because "could not check" is not
    "holds".
    """
    try:
        if arrow == "≥":
            return float(reading) >= float(threshold)
        if arrow == "≤":
            return float(reading) <= float(threshold)
    except (TypeError, ValueError):
        return False
    return False


def evaluate(store: AlertStore, *, detection: dict, device_ref: str, at: str,
             detector: str, thresholds: dict | None = None,
             policy_block: dict | None = None) -> dict:
    """One pass over one detection document. The whole state machine.

    `detection` is a `resource_anomaly.detect()` (or `anomaly.classify()`)
    payload. Its `signals` are the things that are wrong; its `levels` are what
    every graded measurement IS; its `codes_not_evaluated` are the codes nobody
    looked at. All three are needed, and the second and third are the reason
    this function does not simply diff the signal list against the store.

    `detector` names the module that produced the document, and it is REQUIRED.
    It exists because this node has two detectors with overlapping vocabularies,
    and an alert opened by one must not be closed by the other. The rule this
    prevents is concrete: the resource detector's "the signal stopped, and no
    graded measurement contradicts that" is a sound closure for a code it
    grades, and for a code it does not grade it is a false recovery — the disk
    alert opened by the runtime detector would close one tick later on the
    resource detector's silence about a code it never looks at. A row therefore
    remembers who opened it, and only that detector may close it.

    Returns the alerts that were touched, what the pass decided about each, and
    the ones it deliberately did not speak about with the reason. The `held`
    list is not a log: it is the answer to "why was I not told", which is the
    question a person asks about a monitoring system that stayed quiet.
    """
    if not detector:
        raise AlertError(
            "a detection document with no detector name cannot own an alert: "
            "an alert nobody can be asked to re-derive is one nobody can close")
    block = policy_block or policy()
    #: Which codes may occupy a line on a person's card, from the same table
    #: the renderers read — computed once here and never per row. See
    #: `_sayable_codes`. `pending` is governed by it below, which is what makes
    #: this module agree with `alert_digest` about who may be told.
    sayable = _sayable_codes()
    words = severity_words()
    ranks = _ranks()
    cooldowns = dict(block.get("cooldown_seconds") or {})
    if thresholds is not None:
        _THRESHOLD_CACHE.clear()
        _THRESHOLD_CACHE.update(thresholds)

    signals = detection.get("signals") or []
    levels = detection.get("levels") or {}
    ungraded = {str(row.get("code")): row.get("why")
                for row in (detection.get("codes_not_evaluated") or [])
                if isinstance(row, dict)}
    # §21. The subjects the pass ENUMERATED, as opposed to the ones it managed
    # to measure — see `_graded_measurement` for what the difference decides.
    # An EMPTY inventory is deliberately read as "no inventory was taken": a
    # document that enumerated nothing has not established that every subject
    # is gone, and closing every alert on it would be the mass-resolve this
    # whole branch exists to avoid. A document with no `subjects` key at all is
    # evaluated exactly as it was before the key existed.
    inventory = detection.get("subjects")
    subjects = set(inventory) if inventory else None

    alerts = store.alerts()
    touched: list[str] = []
    held: list[dict] = []
    seen: set[str] = set()

    def hold(alert, stage, why):
        held.append({"alert_ref": alert.get("alert_ref"), "code": alert.get("code"),
                     "subject_ref": alert.get("subject_ref"),
                     "state": alert.get("state"), "stage": stage, "why": why})

    def intend(alert, *, at):
        """Set `pending` for a signal that just fired, by §12's rule.

        The rule is stated once, in `_opening_intent`. This is where the row is
        told the answer, and where a row that owes nothing gets its attribution
        into `held` — the list that answers "why was I not told about this",
        which for a code off the face is the only moment anybody can act on.
        """
        intent, why = _opening_intent(str(alert.get("code") or ""), at=at,
                                      sayable=sayable)
        alert["pending"] = intent
        if why:
            hold(alert, "notification", why)
        return intent

    for signal in signals:
        code = str(signal.get("code") or "")
        severity = str(signal.get("severity") or "")
        subject = str(signal.get("subject") or "")
        if severity not in words:
            # `unknown` is a level the detectors use for a code they have no
            # severity for, and it is NOT a mild one. Rounding it to `low` and
            # notifying would report an ungraded finding as a small one, and
            # dropping it silently would make the alert list a function of the
            # severity table rather than of what was found.
            hold({"alert_ref": None, "code": code, "subject_ref": subject,
                  "state": None}, "notification",
                 f"severity {severity!r} is not one of {sorted(words)}; "
                 f"UNKNOWN is not a level and does not notify")
            continue
        if code in ungraded:
            hold({"alert_ref": None, "code": code, "subject_ref": subject,
                  "state": None}, "notification",
                 f"{code} is reported as not evaluated in the same document that "
                 f"carries a signal for it; the signal loses")
            continue

        ref = alert_ref(identity(code=code, device_ref=device_ref,
                                 subject_ref=subject))
        seen.add(ref)
        row = levels.get(f"{code}|{subject}") or {}
        measured = signal.get("measured")
        current = {
            "code": code, "severity": severity, "device_ref": device_ref,
            "subject_ref": subject,
            "resource_ref": resource_ref(subject),
            "measured": measured, "why": signal.get("why"),
            "window": signal.get("window"),
            "provenance": {
                # A signal that dates itself is believed. The process-layer
                # detector cannot: it is a pure function over facts it was
                # handed and calling a clock inside it would make its answer
                # depend on when it was asked. So the DOCUMENT's own observation
                # time is the fallback — `view.anomaly_list` read the process
                # table at that instant and these signals came out of that
                # reading. It says the reading is dated, which is true; it does
                # not claim a freshness, because for a lifetime average the
                # window is the qualifier and `freshness` would be a second,
                # weaker one.
                "source": signal.get("source") or detection.get("answered_by"),
                "evidence_kind": signal.get("evidence_kind"),
                "observed_at": signal.get("observed_at") or detection.get("measured_at"),
                "freshness": (signal.get("freshness") or {}).get("state")
                if isinstance(signal.get("freshness"), dict) else None,
            },
        }
        alert = alerts.get(ref)
        if alert is None:
            alert = {
                "alert_ref": ref,
                "device_ref": device_ref,
                # Who opened it, and therefore the only detector allowed to
                # close it. See the docstring: two detectors share this store.
                "detector": detector,
                "first_seen": at, "last_seen": at, "occurrence_count": 1,
                "state": "OPEN",
                # Where this alert closes, decided once, at the moment there is
                # a graded measurement to decide it from. A later tick may not
                # have a level row at all (the subject stopped being measured),
                # and an alert that had to re-derive this would have to guess.
                "margin_key": row.get("margin_key"),
                "last_notified_at": None, "last_notified_severity": None,
                "last_attempt_at": None, "last_delivery": None,
                "delivery_failure": None,
                "snooze_until": None, "resolved_at": None,
                # Set by `intend` below, not here: whether a message is owed
                # depends on the code's attention level, and the code is not on
                # this dict until `current` is merged into it.
                "pending": None,
                "hold_reason": None, "hold_kind": None,
            }
            alert.update(current)
            intend(alert, at=at)
            alerts[ref] = alert
            touched.append(ref)
            continue

        # An existing alert. The record is updated in all four states, because
        # the observation underneath a SNOOZED or ACKNOWLEDGED alert keeps
        # happening: what those states suppress is the MESSAGE, not the
        # measurement, and an alert whose evidence stopped refreshing would be
        # a person's own decision freezing the world it describes.
        previous = alert.get("severity")
        alert.update(current)
        alert["last_seen"] = at
        alert["occurrence_count"] = int(alert.get("occurrence_count") or 0) + 1
        alert["hold_reason"] = None
        alert["hold_kind"] = None
        touched.append(ref)

        if alert.get("state") == "RESOLVED":
            # The same identity, firing again. This is a new EPISODE of the
            # same alert: the ref does not change (it is the identity of the
            # thing, not of the occurrence), the clock restarts, and it speaks
            # at once — a recurrence is not a repeat, and a person who was told
            # it recovered needs to be told it did not stay recovered.
            alert["state"] = "OPEN"
            alert["first_seen"] = at
            alert["occurrence_count"] = 1
            alert["resolved_at"] = None
            intend(alert, at=at)
            alert["reopened_from"] = previous
            continue

        if alert.get("state") == "SNOOZED":
            until = alert.get("snooze_until")
            remaining = _seconds_between(at, until) if until else None
            if remaining is not None and remaining > 0:
                hold(alert, "notification",
                     f"snoozed for another {_duration(remaining)}")
                continue
            # Expired, and still firing. Back to OPEN, and the ordinary rules
            # apply from here — including the cooldown, which has been running
            # the whole time. A snooze that silently resolved would be a way to
            # make a problem disappear by waiting for it.
            alert["state"] = "OPEN"
            alert["snooze_until"] = None

        speak, why_not = _should_repeat(alert, severity=severity, at=at,
                                        cooldowns=cooldowns, ranks=ranks)
        if speak:
            intend(alert, at=at)
        else:
            hold(alert, "notification", why_not)

    # -- everything that did NOT fire this tick ------------------------------
    for ref, alert in alerts.items():
        if ref in seen or alert.get("state") == "RESOLVED":
            continue
        if alert.get("detector") != detector:
            # Somebody else's row. Its absence from THIS document says nothing
            # about it — this detector never looks at that code.
            continue
        verdict, why, kind = _graded_measurement(alert, levels=levels,
                                                 ungraded=ungraded,
                                                 subjects=subjects)
        if verdict == "RESOLVED":
            alert["state"] = "RESOLVED"
            alert["resolved_at"] = at
            alert["snooze_until"] = None
            alert["hold_reason"] = None
            alert["hold_kind"] = None
            # §10. Exactly one recovery message, and it is the reason
            # `alert.resolved` exists at all: a person who was told about a
            # problem and never told it ended reasonably concludes it did not.
            # `_resolving_intent` is what makes "was told" a fact about this row
            # rather than an assumption: see it for the two ways an ending owes
            # nobody anything.
            alert["pending"], why_not = _resolving_intent(alert, at=at,
                                                          sayable=sayable)
            if why_not:
                hold(alert, "notification", why_not)
            touched.append(ref)
        else:
            alert["hold_reason"] = why
            # The machine-readable half. `unmeasured` and `not_evaluated` are
            # the two that are a LOSS OF OBSERVATION rather than a result, and
            # the only two that a monitoring-integrity finding may be built
            # from — see `notification-contract.yaml#alerts.attention.
            # monitoring_integrity`. `above_floor` is the opposite fact: we
            # looked, the value is still up, and nothing is missing.
            alert["hold_kind"] = kind
            hold(alert, "resolution", why)

    # -- the rows that claim a message nobody can receive ---------------------
    #
    # The two rules above are applied at the two moments a row OPENs and
    # CLOSEs. This is the third moment, and the only one that reaches rows
    # already in the register — 13,207 of them on this node, written by the
    # rules as they stood. Reconciliation is not a separate migration with its
    # own run: every pass brings the rows it owns into line, which is the only
    # way the register's own total stops being the number that is wrong.
    #
    # Only this detector's rows. The other detector's rows were graded by a
    # document this pass never saw, and reconciling them from here would be this
    # detector ruling on another one's evidence — the same mistake the `detector`
    # field exists to prevent.
    #
    # Nothing here touches `state`, `hold_kind` or `hold_reason`. A withdrawn
    # claim is not a resolution and not a hold: the finding is exactly as open,
    # exactly as graded, and exactly as visible as it was. `hold_reason` in
    # particular is left alone on purpose — the digest card reads it on OPEN
    # rows as 「已经不在了」 and reports those as the alerting system's own
    # defect, so writing this reason there would make it say 「gone」 about a
    # thousand findings it is still measuring.
    for ref, alert in alerts.items():
        if alert.get("detector") != detector:
            continue
        why = _withdrawn_intent(alert, at=at, sayable=sayable)
        if why:
            hold(alert, "notification", why)

    store.commit(alerts)
    pending = [row for row in alerts.values() if row.get("pending")]
    pending.sort(key=lambda row: row["alert_ref"])
    return {
        "at": at,
        "device_ref": device_ref,
        "alerts": [alerts[ref] for ref in sorted(set(touched))],
        "alerts_total": len(alerts),
        "pending": pending,
        "held": held,
        "note": ("`held` is why nothing was said, not a log. An alert in it is "
                 "still open, still updating, and will be reported here again "
                 "next pass with the reason that still applies."),
    }


# ---------------------------------------------------------------------------
# The message. §13, and the two events this module raises.
# ---------------------------------------------------------------------------


def recommendation(severity: str) -> str:
    """REVIEW or MONITOR. A suggestion, and stated as one.

    §15 permits a recommendation and forbids it from being read as authority:
    `alert != authorization`. Nothing in this system acts on the answer, and
    there is no code path in which this word reaches a machine.

    `GOVERNANCE_REQUIRED` is also permitted by the contract's `text` block and
    is not produced here yet — no code in this release has a fix that is a
    declaration change rather than a look, and inventing one to make the
    vocabulary look complete is the failure §4 names.
    """
    return {"low": "MONITOR", "medium": "REVIEW", "high": "REVIEW"}.get(
        severity, "MONITOR")


def detail_for(alert: dict, *, at: str) -> str:
    """The block under the headline. Short, labelled, and about one phone screen.

    Every line is either a field the producer measured or an explicit absence.
    Nothing is summarised into a sentence, because a sentence is what a person
    has to parse before they can tell whether it is worth opening, and the
    whole point of §14 is that the expensive process is named here rather than
    hiding inside a total.
    """
    severity = str(alert.get("severity"))
    word = severity_words().get(severity, severity.upper())
    measured = alert.get("measured") or {}
    provenance = alert.get("provenance") or {}
    lines = [f"{word} · {severity}"]
    lines.append(f"measured: {_measurement_line(alert, measured)}")

    top = measured.get("top_contributor")
    if isinstance(top, dict):
        # §14. The largest term of the number above it.
        #
        # Labelled "highest cpu" rather than plain "top contributor", and the
        # label is load-bearing: the group's ranking is by CPU for every code,
        # so on a HIGH_MEMORY_SUSTAINED alert this line names the busiest
        # process, which is not always the one holding the memory. A bare
        # "top contributor" above "3900.0 MB rss" reads as "this process is
        # holding it", and a person who acts on that has been told something
        # this system did not measure. Ranked one way and said so, rather than
        # ranked two ways and disagreeing with itself.
        rss = top.get("rss_bytes")
        lines.append(
            "top contributor (highest cpu): pid {pid} {comm} {cpu}% of one core{mem}".format(
                pid=top.get("pid"), comm=top.get("comm") or "?",
                cpu=top.get("cpu_percent"),
                mem=f", {rss / 1048576:.1f} MB rss" if rss else ""))
    elif alert.get("code") and measured.get("process_count"):
        # A group with no contributor list is a group this node ranked without
        # the process table — reported as an absence, never as "one process".
        lines.append("top contributor: not ranked (no process table in this reading)")

    if measured.get("origin_session") or measured.get("origin_session_state"):
        lines.append("origin session: {} state {}".format(
            measured.get("origin_session") or "unknown",
            measured.get("origin_session_state") or "not reported"))
    ownership = measured.get("ownership")
    if isinstance(ownership, dict) and ownership:
        # §7. WHO this is and WHAT MAY BE DONE ABOUT IT are different
        # questions, and an EXTERNAL answer settles the second one in advance:
        # `lifecycle` is OBSERVE_ONLY, so the tree is somebody's and nothing
        # here may act on it. Without this line the sentence a person receives
        # names a leftover process tree and never says whose, which is both the
        # field §7 asks for and the difference between a finding its reader can
        # act on and one they can only worry about.
        #
        # Every clause is printed as the absence it is when the probe recorded
        # none. A missing session and an elided one must not read the same on
        # one line: "no session recorded" is a fact about the reading, and a
        # dropped clause would be a fact about this formatter.
        lines.append("owner: {} · uid {} · {} · lifecycle {}".format(
            ownership.get("role") or "unattributed",
            ownership.get("uid") or "unrecorded",
            ownership.get("session") or ownership.get("unit")
            or ownership.get("slice") or "no session or unit recorded",
            ownership.get("lifecycle") or "not stated"))

    if alert.get("window"):
        # The qualifier on the number above it. "279% of one core" is a
        # different claim over a lifetime average than over five seconds, and
        # §5's case — a group that has been burning cores for hours — is the
        # one where a reader has to be able to tell them apart. Dropping this
        # was the difference between "it was busy for an hour" and "it was busy
        # for a moment", with the alert unable to say which it meant.
        lines.append(f"window: {alert['window']}")
    lines.append(f"observed: {provenance.get('observed_at') or 'no timestamp'}"
                 f" ({provenance.get('freshness') or 'unknown'},"
                 f" source {provenance.get('source') or 'unknown'})")
    duration = _seconds_between(alert.get("first_seen"), at)
    lines.append("duration: {} (seen {} times)".format(
        _duration(duration) if duration is not None else "unknown",
        alert.get("occurrence_count")))
    lines.append(f"recommendation: {recommendation(severity)} · nothing was "
                 f"stopped, restarted or removed")
    return "\n".join(lines)


def measurement_parts(alert: dict, measured: dict) -> list[dict]:
    """The reading and the threshold it was compared against, as parts.

    ONE PRODUCER, TWO REGISTERS
    ---------------------------
    The evidence block under an incident has always printed these numbers as one
    English sentence, and the card's face now prints the leading one as a
    Chinese field line. They are the same numbers, and they are derived here
    once so that they cannot become two answers: a renderer that re-read
    `used_percent` off the row itself would be a second reader of the register's
    payload, and the day the two disagree is a day the face and the fold beneath
    it print two different figures for one finding.

    Each part is one comparison:

      * `axis`     the register field it came from, so a reader can go and look;
      * `says`     the evidence block's own words, unchanged from when this
                   function was `_measurement_line` and nothing else read it;
      * `face`     the same reading in the card's language, for the one line a
                   phone shows. The two registers are not an accident — the
                   evidence block is quoted in the contract's terms and the face
                   is read by somebody who is not holding the contract;
      * `measured` True for a part that IS a reading, False for the two
                   sentences that are not: the one saying no reading exists for
                   this code, and a caveat, which qualifies a reading rather
                   than being one. The face prints measured parts only, because
                   "no measurement — this code is a declaration" answers "where
                   is the number" and is not a number.

    The threshold is included everywhere it exists because a bare number is not
    a finding: "93.2%" is alarming or unremarkable depending on whether the tier
    is 92% or 80%, and a reader should not have to open the contract to find out
    which.
    """
    parts = []

    def add(axis, says, face, *, measured_=True, faces=()):
        # `face` is the fullest form and stays the part's `face`, so every
        # reader that has always read this key — the model, the JSON payload,
        # the CLI — reads exactly what it read before. `faces` is the SAME
        # sentence in the shorter forms a narrow row can hold, longest first,
        # and only the renderer consumes it: how much of a line fits in 360px
        # is a fact about the row, and the row is not this module's.
        parts.append({"axis": axis, "says": says, "face": face,
                      "faces": ((face,) + tuple(faces)) if face else (),
                      "measured": measured_})

    code_key = threshold_keys().get(str(alert.get("code") or ""))

    def versus(reading, threshold, unit="%", key=None) -> tuple[str, str]:
        """`value op threshold`'s tail, in both registers, or nothing.

        §4: value, operator and threshold TOGETHER, and `crosses()` decides
        whether the operator is earned by this reading. The operator is the one
        thing that differs between the two registers — the evidence block is
        quoted in the contract's terms and spells it in ASCII, the face is read
        by somebody who is not holding the contract. `key` overrides the code's
        own threshold key for the readings whose threshold is a field of
        `measured` rather than the line this code is graded against (the PSI
        pair beside a swap watermark); those field names say `_high` in their
        own right, so the same suffix rule reads them.

        An operator that could not be derived — or that the reading does not
        satisfy — emits nothing, and the string stays exactly as it was before
        this function learned about direction. A face that states a comparison
        nobody declared, or one its own numbers contradict, is worse than a
        face that states none, and both are the failure §4 names.
        """
        if threshold is None:
            return "", ""
        arrow = direction(code_key if key is None else key)
        if arrow and crosses(reading, threshold, arrow):
            return (f" ({arrow} threshold {threshold}{unit})",
                    f"（{arrow} 阈值 {threshold}{unit}）")
        return f" (threshold {threshold}{unit})", f"（阈值 {threshold}{unit}）"

    for key in ("used_percent", "inode_percent"):
        if measured.get(key) is not None:
            tail_says, tail_face = versus(measured[key],
                                          measured.get("threshold_percent"))
            add(key, f"{measured[key]}%" + tail_says,
                f"{measured[key]}%" + tail_face)
    psi = measured.get("memory_pressure")
    if isinstance(psi, dict) and psi:
        # §SWAP. A watermark is not a pressure, and this is the line that stops
        # one being read as the other. The three outcomes are printed
        # separately because they call for different things from whoever reads
        # them — the same three `inventory._swap_note` separates on the
        # advisory path, in the shorter form a card has room for.
        #
        # Nothing here decides whether the stall figure is a problem; it prints
        # the reading beside the threshold it would be compared against, which
        # is what this function does for every other code. The verdict stays in
        # one place, and that place is not the renderer.
        some = ((psi.get("some") or {}) if isinstance(psi.get("some"), dict) else {})
        full = ((psi.get("full") or {}) if isinstance(psi.get("full"), dict) else {})
        some_at = measured.get("psi_memory_some_avg60_percent_high")
        full_at = measured.get("psi_memory_full_avg60_percent_high")
        if some.get("avg60") is None or full.get("avg60") is None:
            # "Cannot tell" is not "fine". `/proc/pressure` is absent on a
            # pre-4.20 kernel and under `psi=0`, and a note that reads an
            # unreadable pressure as no pressure is the conflation this whole
            # pair of fields exists to prevent — it would not become true by
            # being made in a sentence instead of a field.
            #
            # The clause after the reason is chosen by CODE, because the two
            # codes carry this reading in different ROLES: for SWAP_PRESSURE it
            # is the correction to the watermark printed above it, and for
            # MEMORY_PRESSURE_ACTIVE it is the finding itself. One sentence for
            # both would print the swap sentence on a card that has no swap
            # figure above it to point at.
            add("memory_pressure",
                "memory stall was NOT established: "
                + (psi.get("reason")
                   or "memory pressure-stall information was not readable")
                + (" — the swap figure above is a watermark, and a watermark "
                   "is not a measurement of pressure"
                   if alert.get("code") == "SWAP_PRESSURE" else
                   " — what this host is doing to memory could not be "
                   "measured, and unmeasured is not the same as fine"),
                # The face does not carry the reason, which is a sentence from
                # the provider: what a person needs from the first screen is
                # that this reading is missing, and the fold below has the why.
                "内存停顿无法确认（原因见技术详情）")
        else:
            # The threshold field's own name carries the direction here, so it
            # is passed as the key: these two PSI readings are context beside a
            # swap watermark rather than the line THIS code is graded against,
            # and the code's key would answer a different question.
            some_says, some_face = versus(
                some.get("avg60"), some_at, "%",
                key="psi_memory_some_avg60_percent_high")
            full_says, full_face = versus(
                full.get("avg60"), full_at, "%",
                key="psi_memory_full_avg60_percent_high")
            add("memory_pressure",
                f"memory stall some avg60={some['avg60']}%" + some_says
                + f", full avg60={full['avg60']}%" + full_says,
                f"内存停顿 some avg60={some['avg60']}%" + some_face
                + f" · full avg60={full['avg60']}%" + full_face)
    # The other half of a stall reading, and the half that makes it
    # counter-intuitive: this host stalled while holding 188 GiB available
    # (2026-09-29). A reader handed only the stall figure has to supply the
    # rest from a host summary they are not holding, so the availability
    # travels beside it — not as a second verdict, but as the rest of one
    # reading. Absent when the producing summary did not carry it.
    if measured.get("available_percent") is not None:
        add("available_percent",
            f"{measured['available_percent']}% of memory available",
            f"可用内存 {measured['available_percent']}%")
    # The watermark, carried to the card that was raised WITHOUT it. The split
    # between the two axes is only legible if a reader can see that the pressure
    # fired while the watermark had not: the whole finding on 2026-09-29 was a
    # machine stalling at 80.5% of swap, below the 90% the other code grades on,
    # and a stall figure printed alone cannot distinguish that machine from one
    # thrashing its way past the line. Spelled `swap` and not left as a bare
    # percentage, because this line already prints one memory percentage and two
    # numbers that are not obviously different numbers is how a reader supplies
    # the wrong one.
    if measured.get("swap_used_percent") is not None:
        add("swap_used_percent",
            f"swap {measured['swap_used_percent']}% used",
            f"交换区已用 {measured['swap_used_percent']}%")
    # The third axis, and the only place it reaches a person. A watermark says
    # how full; a stall figure says how much work waited; neither says whether
    # pages are moving at all, and "full and quiet" and "full and thrashing"
    # report identical figures on the first two. Pages per second and never
    # bytes — `pswpout` counts pages, and multiplying by a page size here would
    # write an assumption about one kernel into a figure a reader is meant to
    # compare against another host's.
    paging = measured.get("paging")
    if isinstance(paging, dict) and paging.get("state") == "PRESENT":
        add("paging",
            f"paging {paging.get('pswpout_per_second')} pages/s out, "
            f"{paging.get('pswpin_per_second')} pages/s in",
            f"换页 {paging.get('pswpout_per_second')} 页/秒出 · "
            f"{paging.get('pswpin_per_second')} 页/秒入")
    for key in ("size_bytes", "log_bytes", "size_rw_bytes"):
        if measured.get(key) is not None:
            threshold = measured.get("threshold_bytes")
            tail_says, tail_face = versus(
                measured[key] / 1048576,
                None if threshold is None else threshold / 1048576, " MB")
            add(key, f"{measured[key] / 1048576:.1f} MB" + tail_says,
                f"{measured[key] / 1048576:.1f} MB" + tail_face)
    if measured.get("cpu_percent") is not None:
        threshold = measured.get("threshold_percent")
        count = measured.get("process_count") or "?"
        tail_says, tail_face = versus(measured["cpu_percent"], threshold)
        add("cpu_percent",
            f"{measured['cpu_percent']}% of one core across "
            f"{count} process(es)" + tail_says,
            f"{measured['cpu_percent']}% 单核 · {count} 个进程" + tail_face)
    if measured.get("memory_rss_bytes") is not None:
        threshold = measured.get("threshold_bytes")
        tail_says, tail_face = versus(
            measured["memory_rss_bytes"] / 1048576,
            None if threshold is None else threshold / 1048576, " MB")
        add("memory_rss_bytes",
            f"{measured['memory_rss_bytes'] / 1048576:.1f} MB rss" + tail_says,
            f"{measured['memory_rss_bytes'] / 1048576:.1f} MB rss" + tail_face)
    if measured.get("desired_state") is not None \
            or measured.get("actual_derived_state") is not None:
        # §15. The threshold of this comparison IS the declaration, and the two
        # sides are the whole finding. They were in `measured` and never
        # printed: a reader of the detail block was told that a declaration and
        # the world disagreed and not what either side said, on the one code
        # whose entire content is those two words.
        #
        # The provider's own word travels beside the normalized one.
        # `restarting` and `dead` both normalize to STOPPED and they call for
        # different things from whoever reads this.
        #
        # This is the one measurement whose face can outgrow the row, so it
        # carries its shorter forms as well: the declaration and the measured
        # state are the finding and cannot go, the provider's word is the
        # qualifier and is the first thing to give up — and it is given up on
        # the FACE only. The evidence block prints this part's `says`, which
        # keeps `(provider says <raw>)` whichever tier the face took, so the
        # word is one fold away rather than gone.
        declared = measured.get("desired_state") or "undeclared"
        actual = (measured.get("actual_derived_state")
                  or measured.get("actual_state") or "not measured")
        raw = measured.get("actual_raw")
        tail = (f" (provider says {raw})" if raw and str(raw) != str(actual)
                else "")
        add("desired_state",
            f"declared {declared} · measured {actual}" + tail,
            f"声明 {declared} · 实测 {actual}"
            + (f"（生产者说 {raw}）" if tail else ""),
            faces=((f"声明 {declared} · 实测 {actual}（{raw}）",
                    f"声明 {declared} · 实测 {actual}") if tail else ()))
    if not parts:
        # A finding that is a declaration rather than a measurement. Named as
        # such, so the reader does not go looking for a number that does not
        # exist for this code.
        add("why",
            f"{alert.get('why') or 'no measurement — this code is a declaration'}",
            "", measured_=False)
    caveat = measured.get("caveat")
    if caveat:
        # §15's qualifier, carried in full. It is a paragraph because the
        # second reading it names is a specific measured case on this host, and
        # a shortened version would be a sentence without its evidence.
        add("caveat", f"caveat: {caveat}", "", measured_=False)
    return parts


def measurement_face(alert: dict, measured: dict) -> dict | None:
    """The one reading the incident card's face prints, or nothing.

    The FIRST measured part and not a selection by importance, because the
    order above is already the order of how close each reading is to being the
    finding itself — a percentage against its tier before the stall behind it
    before the comparison that is its own threshold.

    Nothing when no part is a reading. A card that printed 「测量 —— 本代码是一个
    声明」 in the field where the other cards print a number would be answering a
    question nobody asked, in the place where the answer to a real one goes. The
    face then carries no measurement line at all, and the fold says why.
    """
    for part in measurement_parts(alert, measured):
        if part["measured"] and part["face"]:
            return part
    return None


def _measurement_line(alert: dict, measured: dict) -> str:
    """The evidence block's sentence: every part, in the order above."""
    return ", ".join(part["says"] for part in measurement_parts(alert, measured))


def notify_for(provider, *, alert: dict, kind: str, device_ref: str, at: str,
               conversation_ref: str, correlation_id: str | None = None):
    """Raise `alert.opened` / `alert.resolved` and hand it to the provider.

    THE producer for both events — the contract says so by name, and this is
    the only function in the tree that constructs them. The provider arrives as
    an argument for the reason §2 gives: ServerOS writes what the fact is, the
    provider owns how it reaches a person, and neither of them is allowed to
    reach into the other. Nothing here imports a transport, so no caller can
    make this module depend on one by accident.

    `correlation_id` distinguishes one message about an alert from the next.
    The transport keys its own de-duplication on the event's idempotency key,
    and that key is built from the correlation id — so re-sending the SAME
    notification (a retry of one attempt) collapses, while a repeat a cooldown
    later is a genuinely new message and must not be swallowed as a duplicate
    of the one it is repeating.
    """
    notifications = _notifications()
    if kind not in ("opened", "resolved"):
        raise AlertError(f"{kind!r} is not an alert notification kind")
    event_name = OPENED if kind == "opened" else RESOLVED
    event = notifications.Event(
        event=event_name,
        device_ref=device_ref,
        at=at,
        correlation_id=correlation_id or f"{alert.get('alert_ref')}:{at}",
        subject_ref=alert.get("subject_ref"),
        reason=alert.get("code"),
        detail=detail_for(alert, at=at),
    )
    return notifications.deliver(provider, event, conversation_ref=conversation_ref)


def pending_for(store: AlertStore, *, at: str) -> list[dict]:
    """What still has to be said, and is allowed to be said now.

    The delivery hop is a separate surface from the evaluation hop on purpose:
    §18 forbids the daemon from becoming a client of any transport, and the
    daemon is what runs the tick. So the daemon decides and records, and a
    surface that is allowed to hold a provider asks this question and performs
    the delivery.
    """
    out = []
    for row in store.alerts().values():
        pending = row.get("pending")
        if not isinstance(pending, dict):
            continue
        not_before = pending.get("not_before")
        # `remaining` and not `elapsed`, and the argument order is the whole
        # reason for the name: `seconds_between(then, now)` reads
        # left-to-right, so `(not_before, at)` is how long ago the earliest
        # moment was. Written the other way round the test below inverts and
        # this function returns exactly the intents that are NOT yet due — a
        # register that opens alerts and then never says any of them, which is
        # silent and looks like a working cooldown. Found by running a pass and
        # finding fifty-five new alerts with nothing pending.
        remaining = _seconds_between(at, not_before)
        if remaining is None:
            continue
        if remaining > 0:
            continue
        out.append({"alert_ref": row["alert_ref"], "kind": pending["kind"],
                    "alert": row})
    out.sort(key=lambda item: item["alert_ref"])
    return out


def record_delivery(store: AlertStore, *, alert_ref: str, kind: str, at: str,
                    delivered: bool, state: str, reason: str | None = None,
                    cooldowns: dict | None = None) -> dict:
    """What we know after trying. §12, and the vocabulary is the point.

    Four outcomes and none of them resolves an alert. DELIVERED means the
    provider accepted the call — not that a person read it. FAILED means it was
    tried and did not arrive. UNSUPPORTED means the transport declined before
    anything was sent. UNKNOWN means nobody could establish whether it can.

    `last_notified_at` is set ONLY for DELIVERED. A message that did not arrive
    was not a notification, and recording one would make "this was announced"
    and "this was supposed to be announced and was not" the same field. The
    intent stays pending either way, with its clock pushed forward by the
    cooldown so the next surface does not retry instantly.
    """
    alerts = store.alerts()
    alert = alerts.get(str(alert_ref))
    if alert is None:
        raise AlertError(f"no alert {alert_ref!r} is held by this store")
    cooldowns = dict(cooldowns or policy().get("cooldown_seconds") or {})
    alert["last_delivery"] = state
    alert["last_attempt_at"] = at
    if delivered:
        alert["last_notified_at"] = at
        alert["last_notified_severity"] = alert.get("severity")
        alert["delivery_failure"] = None
        alert["pending"] = None
    else:
        alert["delivery_failure"] = reason or f"delivery answered {state}"
        cooldown = cooldowns.get(str(alert.get("severity"))) or 0
        alert["pending"] = {"kind": kind,
                            "not_before": _shift(at, int(cooldown)) or at,
                            "last_state": state,
                            "last_reason": reason}
    store.commit(alerts)
    return alert


# ---------------------------------------------------------------------------
# What a person may do to an alert. §11.
# ---------------------------------------------------------------------------
#
# Both of these are LOCAL, and that is the boundary rather than a missing
# feature. `human.transport.ingress` is UNKNOWN and `human.approval.transport`
# is UNSUPPORTED: no documented surface carries a decision from a chat back
# into this node, so a design in which a person could "reply ACK" would be a
# design that read messages nobody verified. Until an inbound surface exists,
# acknowledgement is a CLI action and the Feishu road stays closed — with the
# reason reported by `mcp/human_control.py#capabilities`, not worked around.


def acknowledge(store: AlertStore, ref: str, *, at: str, note: str | None = None) -> dict:
    """A person has seen it, and the condition has NOT gone away.

    Deliberately not a closure. An acknowledged alert that the world fixes
    becomes RESOLVED by the ordinary path; one that nobody fixes stays
    ACKNOWLEDGED and keeps its observation current. There is no code path from
    here to RESOLVED, which is what makes "acknowledged" safe to press.
    """
    alerts = store.alerts()
    alert = _require(alerts, ref)
    if alert.get("state") == "RESOLVED":
        raise AlertError(
            f"{ref} is RESOLVED: acknowledging it would record a person's "
            f"attention against something that is no longer happening")
    alert["state"] = "ACKNOWLEDGED"
    alert["acknowledged_at"] = at
    if note:
        alert["acknowledgement_note"] = note
    store.commit(alerts)
    return alert


def snooze(store: AlertStore, ref: str, *, at: str, seconds: int | None = None) -> dict:
    """Stop saying this, for a while, and nothing else.

    The observation keeps updating and `occurrence_count` keeps rising while
    the snooze runs. When it expires an alert that is still firing is allowed
    to speak again — a snooze that silently resolved would be a way to make a
    problem disappear by waiting, and it would be indistinguishable from a fix.

    "Stop saying this" includes a message that has not been sent yet. The
    intent is withheld rather than dropped and recorded as withheld, because a
    snooze that left the first message deliverable would be a silence with an
    expiry on it, and the person who asked for the silence is the one who would
    be surprised by the message.
    """
    alerts = store.alerts()
    alert = _require(alerts, ref)
    if alert.get("state") == "RESOLVED":
        raise AlertError(
            f"{ref} is RESOLVED; there is nothing left to snooze")
    window = int(seconds if seconds is not None
                 else policy().get("snooze_default_seconds") or 0)
    if window <= 0:
        raise AlertError(
            "a snooze with no window is a resolution by another name; refusing")
    until = _shift(at, window)
    if until is None:
        raise AlertError(f"{at!r} will not parse, so the snooze has no expiry")
    withheld = alert.get("pending")
    if isinstance(withheld, dict):
        # A pending intent outlives a snooze unless it is cleared here, because
        # `pending_for()` answers "is this due yet" without consulting the state
        # — so a person who snoozed an alert whose FIRST message had not gone out
        # would still be notified by the next surface that ran `sync`, for the
        # whole window they asked not to be. Found by a test that snoozed an
        # alert it had deliberately never delivered. The intent is not lost: when
        # the window expires, an alert that is still firing is allowed to speak
        # again by the ordinary path, which re-pends it.
        alert["pending"] = None
        alert["withheld_at"] = at
        alert["withheld_kind"] = withheld.get("kind")
    alert["state"] = "SNOOZED"
    alert["snooze_until"] = until
    alert["snoozed_at"] = at
    store.commit(alerts)
    return alert


def _require(alerts: dict, ref: str) -> dict:
    alert = alerts.get(str(ref))
    if alert is None:
        raise AlertError(
            f"no alert {ref!r} is held by this store. Alerts are identified by "
            f"code, device and subject — `alert list` shows the refs this node "
            f"actually holds")
    return alert


# ---------------------------------------------------------------------------
# Reading. A list a person scans, and one row in full.
# ---------------------------------------------------------------------------


def listing(store: AlertStore, *, at: str | None = None) -> dict:
    """Every alert, most in need of attention first.

    Ordered by state rather than by time, because the question is "what is
    wrong" and not "what happened most recently": a critical alert that has
    been open for three days belongs above a resolved one from this morning,
    and a list sorted by `first_seen` puts them the other way round.
    """
    order = {"OPEN": 0, "ACKNOWLEDGED": 1, "SNOOZED": 2, "RESOLVED": 3}
    ranks = _ranks()
    # Read once, not once per row. Both of these go through `policy()`, which
    # parses the contract document; measured on this node at 10.2 ms. Inside
    # the loop below that is one parse per alert -- 612 of them for a 612-row
    # register, which is the whole of `alert list`'s 6.5 s. The client waits
    # 5.0 s, so the answer a person got was a timeout rather than this node's
    # alerts. Neither mapping depends on the row, so neither may live in the
    # loop that has one.
    words = severity_words()
    rows = list(store.alerts().values())
    rows.sort(key=lambda row: (order.get(str(row.get("state")), 9),
                               -ranks.get(str(row.get("severity")), -1),
                               str(row.get("first_seen"))))
    out = []
    for row in rows:
        pending = row.get("pending") or {}
        duration = _seconds_between(row.get("first_seen"), at) if at else None
        out.append({
            "alert_ref": row.get("alert_ref"),
            "code": row.get("code"),
            "severity": row.get("severity"),
            "severity_word": words.get(str(row.get("severity"))),
            "state": row.get("state"),
            "device_ref": row.get("device_ref"),
            "subject_ref": row.get("subject_ref"),
            "resource_ref": row.get("resource_ref"),
            "first_seen": row.get("first_seen"),
            "last_seen": row.get("last_seen"),
            "duration_seconds": duration,
            "occurrence_count": row.get("occurrence_count"),
            "last_notified_at": row.get("last_notified_at"),
            "last_delivery": row.get("last_delivery"),
            "delivery_failure": row.get("delivery_failure"),
            "pending": pending.get("kind"),
            "pending_not_before": pending.get("not_before"),
            "snooze_until": row.get("snooze_until"),
            "resolved_at": row.get("resolved_at"),
            "hold_reason": row.get("hold_reason"),
            "hold_kind": row.get("hold_kind"),
            "provenance": row.get("provenance"),
        })
    return {"schema": SCHEMA, "count": len(out),
            "by_state": {state: sum(1 for r in out if r["state"] == state)
                         for state in states()},
            "alerts": out,
            "note": ("an open alert with a `hold_reason` is one this node has "
                     "decided not to speak about again yet, or whose closing "
                     "condition is not met. It is not a closed one. "
                     "`hold_kind` is the machine-readable half: `unmeasured` "
                     "and `not_evaluated` mean nobody looked, `above_floor` "
                     "means somebody looked and the value is still up, and "
                     "only the first two are a loss of observation.")}


def main(argv: list[str] | None = None) -> int:
    """Read one store. A diagnostic, not the product surface."""
    import argparse
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--state", required=True,
                        help="the alerts state file to read")
    parser.add_argument("--at", help="the instant to report durations as of")
    args = parser.parse_args(argv)
    store = AlertStore(args.state)
    json.dump(listing(store, at=args.at), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
