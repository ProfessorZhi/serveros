#!/usr/bin/env python3
"""§Q2 — what CHANGED, batched into one message, and nothing else.

WHY THIS EXISTS
---------------
`runtime/alerting.py` answers "is this one anomaly open, and may we say so".
`runtime/notifications.py` answers "what sentence is this, and can the
transport carry it". Between them they can say a great deal, and the shape
they say it in is ONE MESSAGE PER ALERT. On a shared host with a standing
backlog that shape is the failure mode: the register held hundreds of pending
intents, each one a separate delivery, and the only transport the engine had
was a session injection — so "tell a person about the disk" meant "start an
agent turn about the disk".

This module is the missing middle: several alerts, one message, and a rule for
WHEN that message is worth sending. It is a COMPOSER, not a second detector.
It reads the register `alerting.py` already owns, compares it against what the
person was last told, and produces a document. It does not probe, does not
decide whether an anomaly is real, and does not speak to any transport.

WHAT IT IS NOT ALLOWED TO DO
----------------------------
Nothing here stops, restarts, kills, prunes or reclaims anything, and there is
no call to a signal, a socket, a subprocess or the Docker API. `alert !=
authorization` holds verbatim: the digest is a finding a person is told about.

It also does not become a second authority over the register. Every field it
prints was already in the store or is derived from it by arithmetic; the only
state it owns is the DELIVERY CURSOR — what this node has already said to a
person — which is a fact about the conversation and not about the world.

THE FOUR DELTAS (§Q2)
---------------------
    NEW       the register holds an identity the person has not been told
              about, and it appeared after the baseline
    ESCALATED a known identity whose severity has gone UP since they were told
    RECOVERED a known identity that has since closed
    REOPENED  a known identity that closed and is now open again

DEDUPE
------
The dedupe key is `alerting.identity()` — code + device + subject — which is
also the alert's own `alert_ref`. Reusing it rather than minting a second key
is the whole of the requirement: two systems that disagree about whether two
sightings are the same alert is how a person gets told twice.

The one thing §Q1 changed under this module is worth stating here, because it
is the reason the key can be trusted at all: a ref used to be a group's LAST
CGROUP PATH SEGMENT, so six scopes called `init` shared one ref and the dedupe
key collapsed them. That is fixed in `runtime/graph.py#_disambiguate_ids`, and
this module's correctness rests on it.

THE BASELINE
------------
A backlog is not news. The first time this module runs it records
`ALERT_DELIVERY_BASELINE_AT` and snapshots every identity already in the
register as ALREADY KNOWN and NEVER ANNOUNCED. Those rows are counted, are
visible in the standing section, and are never emitted as NEW — an alert that
was open for a week before anybody wired a digest is not an event that just
happened. They can still ESCALATE, because "it got worse" is news whoever was
told what, and that is not retroactive.

WHAT A PERSON IS TOLD, AND WHAT IS ONLY COUNTED
-----------------------------------------------
`shown` is what fits on one card: every delta, ordered by severity and then by
kind, up to the declared top-N, with the remainder reported as a count rather
than dropped. LOW-severity rows are in the document and are NOT eligible for
an out-of-band send — the declared `immediate_severities` decides that, so
"this is urgent" is policy and not a judgement made here.

THE FROZEN VERSION
------------------
Composing and recording are two moments, and a transport sits between them.
The register can move in that gap, and the fields this module needs in order to
record a delivery correctly — severity, state, and which APPEARANCE of an
identity a row was about — are exactly the fields the register is free to
change. Reading them at record time is a race with a silent symptom: the card
said MEDIUM, the detector escalated to HIGH while the send was in flight, the
receipt wrote HIGH, and the escalation was never announced to anybody because
the comparison that would have found it had already been satisfied.

So `compose` freezes the version it produced — the payload hash over the
document as composed, the generation, and a per-row record of the severity,
state and EPISODE each shown row asserted — and `record` writes THOSE. The
world is then free to move on: a severity change is an escalation pending
against what the person was told, a close is a RECOVERED, a reopen is a
REOPENED, and none of them was consumed by the receipt of a card that did not
mention it.

Recording the payload is also what makes the receipt checkable. A delivery is
recorded against a version, not against a clock, and a caller that hands over
a frozen block and rows that disagree is refused rather than reconciled.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

CONTRACT = _HERE / "notification-contract.yaml"
SCHEMA = "serveros/alert-digest.v1"
CURSOR_SCHEMA = "serveros/alert-delivery.v1"

#: The document's own statement about the version of itself that a send would
#: carry. It sits ON the document rather than beside it so that the thing
#: `alert digest --json` prints and the thing a renderer hands back to
#: `alert digest-record` are ONE artifact: a hash kept next to the payload is a
#: hash that can be separated from it, and the separation is the whole failure
#: this field exists to close.
FROZEN_FIELD = "frozen"
FROZEN_SCHEMA = "serveros/alert-digest-frozen.v1"

#: What makes two sightings of one identity the SAME appearance. The key alone
#: is not enough — `alert_ref` is reused by design, which is the point of the
#: dedupe key and also what lets a resolved-then-reopened alert occupy the same
#: row as the alert it replaced. `first_seen` and `resolved_at` are the two
#: fields the register stamps when an appearance begins and ends, so a tuple of
#: them names an EPISODE: two digests of one continuous OPEN period share it,
#: and a close-and-reopen does not.
EPISODE_FIELDS = ("first_seen", "resolved_at")

#: The four delta kinds, in the order a card reads them. Named here so the
#: contract's `alerts.digest.kinds` has a literal to check against and so the
#: vocabulary exists in exactly one place.
NEW = "NEW"
ESCALATED = "ESCALATED"
RECOVERED = "RECOVERED"
REOPENED = "REOPENED"
KINDS = (NEW, ESCALATED, RECOVERED, REOPENED)

#: How a kind sorts on the card. A thing that is wrong now beats a thing that
#: has stopped being wrong, so the reader's eye lands on the first group even
#: when the second is larger.
_KIND_ORDER = {NEW: 0, ESCALATED: 0, REOPENED: 1, RECOVERED: 2}

DEVICE_FALLBACK = "device:unknown"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _alerting():
    return _load("serveros_runtime_alerting", _HERE / "alerting.py")


def _graph():
    return _load("serveros_runtime_graph", _HERE / "graph.py")


def _store_io():
    # The same atomic write every other durable document in this tree uses.
    # A second implementation of "write a JSON file without a torn read" is a
    # second chance for one of them to be wrong.
    return _load("serveros_runtime_agent_control_store",
                 _HERE / "agent_control_store.py")


def _presentation():
    # Loaded at CALL time and not at import time, because `presentation.py`
    # loads THIS module from its own top level (`presentation.alert_model`
    # models the digest) — a module-level load here would be a cycle. Loaded
    # lazily for a weaker version of the same reason as `_alerting`/`_graph`.
    return _load("serveros_runtime_presentation", _HERE / "presentation.py")


class DigestError(RuntimeError):
    pass


def policy() -> dict:
    """The `alerts.digest:` block. Every number this module compares against.

    Read through `alerting.policy()` rather than from the file again, so that
    there is one reader of this contract and one place for it to be wrong. The
    block is REQUIRED: a digest with a hardcoded interval would be a cadence
    nobody reviews, and the file is where this tree keeps cadences.
    """
    block = (_alerting().policy() or {}).get("digest")
    if not isinstance(block, dict):
        raise DigestError(
            f"{CONTRACT} declares no `alerts.digest` policy. This module has no "
            f"default interval, no default top-N and no default severity split "
            f"to fall back to: a threshold written next to the comparison that "
            f"uses it is a number nobody reviews")
    return block


# The kinds that describe something happening rather than something having
# stopped. `RECOVERED` is deliberately not one of them: see `decision`.
_PROACTIVE_KINDS = (NEW, ESCALATED, REOPENED)


def proactive_severities(block: dict | None = None) -> tuple:
    """Which severities may make a card exist once the interval has elapsed.

    Read from the contract and REQUIRED, for the same reason the interval is:
    a floor written next to the comparison that uses it is a number nobody
    reviews. An empty or missing list would mean "nothing may ever cause a
    card at the interval", which is a different product, not a default.

    NECESSARY AND NOT SUFFICIENT. A severity on this list says a finding may
    cause a card; `may_interrupt` is the other half and says the card would
    print it. `decision` reads the two together; neither is derived from the
    other and a caller that reads this one alone is reading half a gate.
    """
    declared = (block or policy()).get("proactive_severities")
    if not isinstance(declared, (list, tuple)) or not declared:
        raise DigestError(
            f"{CONTRACT}#alerts.digest.proactive_severities is {declared!r}. "
            f"Without it the cadence has no floor and every hour with any row "
            f"at all becomes a card, which is the shape this key exists to "
            f"prevent")
    return tuple(str(word) for word in declared)


def may_interrupt(row: dict, *, table: dict | None = None) -> bool:
    """Whether this row may occupy a LINE on the card — and so cause one.

    The contract keeps two axes and says they are read TOGETHER, neither
    derived from the other: `severity` answers "how bad is what was measured",
    `attention` answers "may this person be interrupted". `decision` used only
    the first, which is the collapse the contract warns about in its own words
    — "drop 值得人知道 and the axis collapses into `severity_words` restated".

    Measured on this host 2026-09-24 with that collapse in place: 460 findings
    at high/medium — 434 `HIGH_MEMORY_SUSTAINED` and 24 `HIGH_CPU_SUSTAINED`,
    both `consumption`, which the contract carries at WATCH — caused a card
    every hour whose face was empty, so the card arrived saying
    `✅ 没有需要处理的变化` and taught its reader to skip it. The face was
    RIGHT about those rows: they are true measurements and not violated
    expectations. The gate was wrong, and wrong in the direction that spends
    the one surface a real finding has.

    The table is read through `presentation` and never by flattening
    `alerts.attention.codes` here: the contract says NO OTHER FILE MAY ASSIGN AN
    ATTENTION LEVEL, and this module assigning one would be a second reader of
    one axis.

    `criticality` is not consulted, and does not need to be. `escalation` moves
    REVIEW to ACTION_REQUIRED and BOTH are on the face, so the answer is
    invariant under it — which is what lets the answer be a TABLE read once
    rather than a decision re-made per row. `test_attention` holds that
    invariant, so a contract whose `escalation.to` named a level OFF the face
    fails there rather than going quiet here.

    An UNDECIDED code answers False, and that is not a quieting: the code is
    counted in `presentation.alert_model`'s own `undecided` bucket, which is
    reported as the hole in the declaration that it is. A finding the renderer
    cannot print is not one that may cause a card — a card caused by it would
    be the same empty card this function exists to stop.

    A caller that HAS ROWS IN HAND MUST PASS `face_table()`, and `decision`
    does. The default here builds one table for one row, because a predicate
    that cannot be called with one argument is a worse predicate; but building
    it inside a loop is the defect `face_table` documents, and it is measured
    there rather than argued.
    """
    table = face_table() if table is None else table
    return bool(table.get(str(row.get("code") or "")))


def face_table() -> dict:
    """`{code: may it occupy a line}`, read from the contract ONCE per caller.

    The answer is a property of the CONTRACT and of no row. `attention` decides
    which codes may be printed; `severity`, which the rows carry, is the other
    axis and not this one. So the table is built once and applied per row —
    the same rule `alerting.listing` had to learn in the same way, in its own
    words: "Read once, not once per row."

    What breaking that rule costs is not a rounding error. Measured on this node
    2026-09-24: the first revision of `may_interrupt` asked
    `presentation.attention_of` once per row, and `presentation._contract()`
    re-reads and re-parses a 46 KB YAML document on every call — 53.8 ms for one
    row. Against the real register that came to 459 rows x 53.8 ms = 24.7 s
    inside `alert.digest`, whose client waits 5.0 s. The gate answered correctly
    and the node stopped being answerable: `serverosctl alert digest` timed out,
    so the card that was correctly suppressed arrived as a FAILED command
    instead of a quiet one. A fix that makes the daemon unreachable is not a fix.

    The levels come from `presentation.code_expectations()`, which is
    `presentation`'s OWN flattening of the three buckets and not a second
    reading of `alerts.attention.codes` by this module — the contract says NO
    OTHER FILE MAY ASSIGN AN ATTENTION LEVEL, and a table written out here would
    be exactly that. An UNDECIDED code is absent from it and answers False,
    which is what `attention_of` answers for it too: `level=None` is not on the
    face, and the code is reported as a hole by `alert_model`'s own `undecided`
    bucket rather than quietly levelled here.
    """
    presentation = _presentation()
    face = tuple(presentation.attention_policy().get("on_the_face") or ())
    return {str(code): str(entry.get("level") or "") in face
            for code, entry in presentation.code_expectations().items()}


def severity_ranks() -> dict:
    """low < medium < high, read from the contract's own order.

    Derived from `alerts.severity_words` rather than restated, so that adding a
    level to the contract cannot leave this module ranking it as unknown.
    """
    return {word: index
            for index, word in enumerate(_alerting().severity_words())}


def _seconds_between(then, now) -> float | None:
    return _graph().seconds_between(then, now)


# ---------------------------------------------------------------------------
# The cursor. One document, beside the register, written the same way.
# ---------------------------------------------------------------------------


def cursor_path(alert_state_path) -> Path:
    """Where the delivery cursor lives, given where the register lives.

    A SIBLING of the register and not a key inside it. The register is alert
    TRUTH — what is wrong — and its document is rewritten whole on every
    evaluation pass; the cursor is what this node has already said to a person,
    which is a fact about a conversation. Putting them in one file would mean
    an evaluation pass rewrote the delivery ledger, and a torn or refused write
    would then lose both at once.
    """
    return Path(alert_state_path).with_name("delivery.json")


class DeliveryCursor:
    """What this node has told a person, and when.

    Not a historian and not a queue. One record per identity this node has
    SPOKEN ABOUT, plus the two clocks the cadence needs: when the baseline was
    taken, and when a digest last actually arrived.

    An unreadable cursor is REFUSED rather than replaced, exactly as the alert
    store is. Starting from an empty cursor would make every open alert look
    like it had never been announced — the retroactive spam this module exists
    to prevent, produced by its own failure path.
    """

    def __init__(self, path) -> None:
        self.path = Path(path)
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"schema": CURSOR_SCHEMA, "keys": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DigestError(
                f"{self.path} exists and cannot be read ({type(exc).__name__}: "
                f"{exc}). Refusing to start with an empty delivery cursor: "
                f"every alert in the register would be announced again as if "
                f"it were new.") from exc
        if not isinstance(data, dict):
            raise DigestError(f"{self.path} is not a JSON object")
        data.setdefault("keys", {})
        data["schema"] = CURSOR_SCHEMA
        return data

    # -- reads ------------------------------------------------------------

    def baseline_at(self) -> str | None:
        return self._data.get("baseline_at")

    def generation(self) -> int:
        return int(self._data.get("generation") or 0)

    def last_digest_at(self) -> str | None:
        return self._data.get("last_digest_at")

    def last_attempt_at(self) -> str | None:
        return self._data.get("last_attempt_at")

    def keys(self) -> dict:
        return json.loads(json.dumps(self._data["keys"]))

    def known(self, key: str) -> dict | None:
        row = self._data["keys"].get(str(key))
        return dict(row) if isinstance(row, dict) else None

    def announced(self, key: str) -> bool:
        return bool((self.known(key) or {}).get("announced"))

    def summary(self) -> dict:
        keys = self._data["keys"]
        return {
            "schema": CURSOR_SCHEMA,
            "path": str(self.path),
            "baseline_at": self._data.get("baseline_at"),
            "generation": self.generation(),
            "last_digest_at": self._data.get("last_digest_at"),
            "last_attempt_at": self._data.get("last_attempt_at"),
            "last_delivery": self._data.get("last_delivery"),
            "delivery_failure": self._data.get("delivery_failure"),
            "last_payload": self._data.get("last_payload"),
            "known_keys": len(keys),
            "announced_keys": sum(1 for row in keys.values()
                                  if isinstance(row, dict) and row.get("announced")),
        }

    # -- the one write ----------------------------------------------------

    def commit(self, *, baseline_at=None, generation=None, last_digest_at=None,
               last_attempt_at=None, last_delivery=None, delivery_failure=None,
               keys=None, last_payload=None) -> dict:
        """The whole document, in one atomic write.

        Whole-document rather than per-record for the same reason the alert
        store is: recording a delivery advances the generation, the clock and
        every announced key in the same instant, and a row-by-row write would
        have a window in which a key is marked announced in a generation that
        was never sent.

        `last_payload` is the receipt for the version that was announced: the
        generation, the payload hash, the version string and how many rows
        travelled. It is a separate field from `last_delivery` because the two
        answer different questions — `last_delivery` says what the transport
        did, and this says WHICH document it did it to. A cursor that recorded
        only the outcome would be unable to tell a re-delivery of one card from
        a delivery of the next one.
        """
        if baseline_at is not None:
            self._data["baseline_at"] = baseline_at
        if generation is not None:
            self._data["generation"] = int(generation)
        if last_digest_at is not None:
            self._data["last_digest_at"] = last_digest_at
        if last_attempt_at is not None:
            self._data["last_attempt_at"] = last_attempt_at
        if last_delivery is not None:
            self._data["last_delivery"] = last_delivery
        self._data["delivery_failure"] = delivery_failure
        if last_payload is not None:
            self._data["last_payload"] = last_payload
        if keys is not None:
            self._data["keys"] = dict(keys)
        _store_io()._atomic_write(self.path, self._data)
        return self.summary()


def ensure_baseline(cursor: DeliveryCursor, store, *, at: str,
                    device_ref: str) -> dict:
    """Take the baseline once, and never again.

    `ALERT_DELIVERY_BASELINE_AT` is the moment this node started having a
    delivery ledger at all. Everything already in the register at that instant
    is recorded as known-and-never-announced, which is what makes the backlog
    countable and un-retroactive: the NEXT call finds a baseline and does
    nothing, so a restart cannot re-baseline and quietly swallow a real delta.

    Idempotent by construction rather than by a flag: the work is "if there is
    no baseline, write one", and the second caller's condition is already false.
    """
    if cursor.baseline_at():
        return {"baseline_at": cursor.baseline_at(), "taken": False,
                "note": "the baseline was already taken; this call changed nothing"}
    keys = {}
    for row in store.alerts().values():
        ref = str(row.get("alert_ref") or "")
        if not ref:
            continue
        keys[ref] = {
            "announced": False,
            "at": at,
            "kind": None,
            "severity": row.get("severity"),
            "state": row.get("state"),
            "generation": 0,
        }
    cursor.commit(baseline_at=at, keys=keys)
    return {"baseline_at": at, "taken": True, "baseline_keys": len(keys),
            "note": ("every identity already in the register is recorded as "
                     "known and never announced. It is not a delta and will "
                     "never be reported as one; it stays visible in the "
                     "standing section and in the register itself")}


# ---------------------------------------------------------------------------
# The deltas. Pure: the register, the cursor, and the baseline.
# ---------------------------------------------------------------------------


def _owner_of(alert: dict) -> dict | None:
    """Whose tree this is, as the producer measured it -- or None.

    `measured.ownership` is written by the attribution round from the cgroup
    path itself: the role it read, the uid it read, and the clause it read them
    from. It is the answer to §7's "owner-lifecycle if known", and it has been
    on the record the whole time without ever reaching a row.

    Passed through as the producer's OWN vocabulary rather than translated
    here. `EXTERNAL_USER_SESSION` is a member of a closed set that this module
    does not own, and a digest that renamed it would be a second place the set
    is written down -- the same reason `severity_word` is read from the
    contract instead of composed at the call site.

    Returns None and not an empty dict when nothing was attributed. A fabricated
    owner would be worse than an absent one: the reader cannot tell the two
    apart on a card, and one of them is a claim about a real login.
    """
    ownership = (alert.get("measured") or {}).get("ownership")
    if not isinstance(ownership, dict) or not ownership:
        return None
    return {
        "class": ownership.get("class"),
        "role": ownership.get("role"),
        "lifecycle": ownership.get("lifecycle"),
        "uid": ownership.get("uid"),
        "session": ownership.get("session"),
        "unit": ownership.get("unit"),
        "provenance": ownership.get("provenance"),
    }


#: The comparison's own fields, carried from the register when the alert has
#: them. §15's code is the one whose `what` cannot be written without both
#: sides: "有一个对象的实际状态与声明的期望状态不一致" is equally true of a
#: stopped container and of a failed probe, and a person has to be told which
#: service and in what way. They sit on the register row as `measured`, because
#: that is where the signal put them.
#:
#: `actual_derived_state` and not `actual_state`: the derived word is the one
#: the health model classified (`NOT_RUNNING`), and the liveness word beside it
#: (`STOPPED`) means "at rest" on its own. A renderer that read the liveness
#: word would print a service as stopped that the comparison just called out of
#: its desired state.
COMPARISON_FIELDS = ("desired_state", "actual_state",
                     "actual_derived_state", "actual_raw")

#: The numeric readings a detector put in `measured`, and the threshold each was
#: compared against. Carried onto the digest row because `alerting.detail_for`
#: is the block that turns them into `92.0% (threshold 90%)`, and the row is the
#: only thing it is ever handed.
#:
#: Dropping them was not neutral, and it was measured rather than argued: on
#: 2026-09-29 this node's register held 10,844 rows and every one carried
#: `measured` -- `SWAP_PRESSURE` at `used_percent 92.0` against
#: `threshold_percent 90.0`, `ROOT_FS_PRESSURE` at 87.6 against 85.0,
#: `HIGH_MEMORY_SUSTAINED` with `memory_rss_bytes` and `threshold_bytes` -- while
#: the digest carried **0 of 9,273 rows** with any numeric measurement key at
#: all. So `_measurement_line` fell through to its own fallback and printed the
#: alert's `why`, which is a sentence the detector wrote about the finding, in
#: the position where the contract (`notification-contract.yaml`, `text.
#: detail_shape`) asks for "the measurement, with the threshold it was compared
#: against". The card and the notification both said a threshold had been
#: crossed without ever printing the number or the threshold.
#:
#: Listed by name rather than copied whole. `measured` holds whatever the
#: producing detector chose to put there, and handing it through entire would
#: make the evidence block depend on arbitrary per-code payload -- a detector
#: adding a key would thereby add a line to a person's card. These are the keys
#: `_measurement_line` already knows how to render; a new one is added here on
#: purpose, beside the branch that will print it.
#:
#: Two members are structures rather than scalars, and both are here because a
#: renderer reads them: `top_contributor`, which §14's line expands, and
#: `memory_pressure`, which is the SWAP_PRESSURE pair below in full.
#:
#: `memory_pressure` is the reading that keeps a swap watermark from being read
#: as pressure. It is carried whole rather than flattened into a verdict
#: because the two renderers that consult it — the card and
#: `inventory._swap_note` — ask the same question of the same bytes, and a
#: pre-decided boolean would be a third answer to it. Its two thresholds travel
#: by their policy names for the same reason `_swap_note`'s note does: they are
#: the keys a reader would change in the contract, so a card that names them
#: can be argued with.
MEASUREMENT_FIELDS = ("used_percent", "inode_percent", "threshold_percent",
                      "used_bytes", "total_bytes", "size_bytes", "log_bytes",
                      "size_rw_bytes", "threshold_bytes", "cpu_percent",
                      "process_count", "memory_rss_bytes", "top_contributor",
                      "memory_pressure",
                      "psi_memory_some_avg60_percent_high",
                      "psi_memory_full_avg60_percent_high")


def _row_of(alert: dict, *, at: str, ranks: dict, words: dict) -> dict:
    duration = _seconds_between(alert.get("first_seen"), at)
    severity = str(alert.get("severity") or "")
    measured = alert.get("measured")
    measured = measured if isinstance(measured, dict) else {}
    return {
        "key": alert.get("alert_ref"),
        "code": alert.get("code"),
        "severity": severity,
        "severity_word": words.get(severity, severity.upper()),
        "severity_rank": ranks.get(severity, -1),
        "subject_ref": alert.get("subject_ref"),
        "resource_ref": alert.get("resource_ref"),
        "state": alert.get("state"),
        "first_seen": alert.get("first_seen"),
        "resolved_at": alert.get("resolved_at"),
        "duration_seconds": duration,
        "occurrence_count": alert.get("occurrence_count"),
        # §7/§8. Present when the producer attributed this group to an owner,
        # absent when it did not. A renderer reads it if it is there and falls
        # back to the subject if it is not -- the card is allowed to be older
        # than the daemon, and this field is what makes that safe.
        "owner": _owner_of(alert),
        # Where the reading came from and when. Carried because the layer above
        # reads it: `presentation.certainty_of` answers "how much is this
        # evidence worth" from `provenance`, and its own default is UNKNOWN.
        # Leaving the field off this projection did not make the card say
        # UNKNOWN out loud, it made the question unanswerable one layer down
        # while looking, from the reader's side, exactly like a producer that
        # had recorded nothing.
        "provenance": alert.get("provenance"),
        # Qualified evidence. §15 attaches a caveat to a mismatch resting on a
        # process match, because on this host that negative result has two
        # readings -- the process is gone, or it is running behind a visibility
        # boundary -- and the comparison cannot tell them apart. It travels
        # inside `measured`, which is the only part of a signal `alerting`
        # copies onto the record.
        "caveat": measured.get("caveat"),
        # Present on every row and null where the alert has no comparison. A
        # missing key and a null key read differently, and only one of them
        # says "this code has no such measurement".
        **{field: measured.get(field) for field in COMPARISON_FIELDS},
        # The numbers, for the same reason and by the same shape rule: a key is
        # present and null when the alert has no such measurement, so "this code
        # was not measured on this axis" stays distinguishable from "this
        # projection forgot to carry it" -- which is exactly the confusion the
        # omission produced. See `MEASUREMENT_FIELDS`.
        **{field: measured.get(field) for field in MEASUREMENT_FIELDS},
    }


def deltas(store, cursor: DeliveryCursor, *, at: str,
           policy_block: dict | None = None) -> dict:
    """Classify the register against what the person was last told.

    Returns the delta rows and the two counts that are deliberately NOT rows:
    the backlog (open before anybody wired a ledger) and the silent
    resolutions (identities that closed without ever being announced). Both are
    reported so that "nothing happened this hour" and "twelve things happened
    and nobody was told about any of them, by design" cannot be read as the
    same sentence.
    """
    block = policy_block or policy()
    include = tuple(block.get("include_severities") or ())
    immediate = tuple(block.get("immediate_severities") or ())
    ranks = severity_ranks()
    words = _alerting().severity_words()
    baseline = cursor.baseline_at()
    if baseline is None:
        raise DigestError(
            "no baseline has been taken, so nothing here knows what the person "
            "was already told. Call `ensure_baseline` first — a delta computed "
            "without one is the backlog being announced as news")

    rows: list[dict] = []
    held: list[dict] = []
    silent_resolutions = 0
    backlog = 0
    standing: dict[str, int] = {}
    known = cursor.keys()

    for alert in store.alerts().values():
        ref = str(alert.get("alert_ref") or "")
        if not ref:
            continue
        state = str(alert.get("state") or "")
        standing[state] = standing.get(state, 0) + 1
        record = known.get(ref)
        was_announced = bool((record or {}).get("announced"))
        first_seen = str(alert.get("first_seen") or "")

        # Still open, and the last pass could not close it. Carried as a
        # SEPARATE list from `rows` because it is not a delta — nothing about
        # it changed — and the only reason to carry it at all is that one of
        # its kinds is a loss of observation that nothing else reports. It is
        # never announced: an open alert that was not worth saying last hour is
        # not news this hour for being an hour older.
        #
        # `hold_kind` and not `hold_reason`: the two kinds that mean nobody
        # looked and the kind that means somebody looked and the value is still
        # up are opposite facts in similar prose, and a consumer that had to
        # tell them apart by matching sentences would break the first time one
        # was reworded.
        if state == "OPEN" and alert.get("hold_kind"):
            held.append({
                "alert_ref": ref, "code": alert.get("code"),
                "subject_ref": alert.get("subject_ref"),
                "severity": alert.get("severity"),
                "hold_kind": alert.get("hold_kind"),
                "hold_reason": alert.get("hold_reason"),
                "first_seen": first_seen,
                "occurrence_count": alert.get("occurrence_count"),
                "was_announced": was_announced,
            })

        # Open, never announced, and it was already open when the baseline was
        # taken. That is the backlog, and it is counted rather than announced.
        if not was_announced and record is not None and first_seen < baseline \
                and state != "RESOLVED":
            backlog += 1

        kind = None
        if state == "RESOLVED":
            told_state = (record or {}).get("state")
            if told_state != "RESOLVED":
                # UNANNOUNCED AND PRE-BASELINE WAS NEVER NEWS, AND ITS CLOSURE
                # IS NOT NEWS EITHER. A person cannot be told a thing recovered
                # when they were never told it was broken; with a backlog this
                # is the difference between a usable card and a wall of ✅.
                # A POST-baseline alert that opened and closed inside one
                # interval is the opposite case and IS reported: it is the only
                # chance anybody gets to hear about it at all.
                if was_announced or first_seen >= baseline:
                    kind = RECOVERED
                else:
                    silent_resolutions += 1
        elif state == "OPEN":
            if record is None:
                # Not in the cursor at all: it opened after the baseline was
                # snapshotted.
                kind = NEW
            elif (record or {}).get("state") == "RESOLVED":
                kind = REOPENED
            else:
                told_rank = ranks.get(str((record or {}).get("severity")), -1)
                now_rank = ranks.get(str(alert.get("severity")), -1)
                if now_rank > told_rank:
                    kind = ESCALATED
        # ACKNOWLEDGED and SNOOZED are what a PERSON did to an alert. They are
        # not deltas: the identity was already announced, the condition has not
        # changed, and a card that re-announced somebody's own acknowledgement
        # back at them would be the system talking to itself.

        if kind is None:
            continue
        row = _row_of(alert, at=at, ranks=ranks, words=words)
        row["kind"] = kind
        row["was_announced"] = was_announced
        row["backlog"] = (record is not None and not was_announced
                          and first_seen < baseline)
        row["immediate"] = (kind in (NEW, ESCALATED, REOPENED)
                            and row["severity"] in immediate)
        if include and row["severity"] not in include:
            # Declared out of the digest entirely. Kept out of `rows` and
            # counted, never dropped silently.
            continue
        rows.append(row)

    rows.sort(key=lambda r: (-r["severity_rank"], _KIND_ORDER.get(r["kind"], 9),
                             str(r.get("first_seen") or "")))
    return {
        "at": at,
        "baseline_at": baseline,
        "rows": rows,
        "held": held,
        "counts": {kind: sum(1 for r in rows if r["kind"] == kind) for kind in KINDS},
        "severity_counts": {sev: sum(1 for r in rows if r["severity"] == sev)
                            for sev in ("high", "medium", "low")},
        "immediate": [r["key"] for r in rows if r["immediate"]],
        "standing": standing,
        "backlog": backlog,
        "silent_resolutions": silent_resolutions,
    }


def _canonical(blob) -> bytes:
    """One document, one byte string, whatever the serialiser felt like.

    Sorted keys and no incidental whitespace, because the hash is taken over
    this and a reader who reformats the document must not thereby produce a
    different version number for the same payload.
    """
    return json.dumps(blob, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def episode_of(row: dict) -> str:
    """The token that names ONE appearance of an identity.

    Not the alert_ref: that is the identity's stable name and is deliberately
    reused, so a token built from it alone would call a reopen and the alert it
    reopened one thing. Not the severity either — a severity change is a delta
    WITHIN an episode, and the record of what a person was told has to survive
    it rather than be replaced by it.
    """
    blob = "|".join([str(row.get("key") or "")]
                    + [str(row.get(field)) for field in EPISODE_FIELDS])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def row_version(row: dict) -> dict:
    """What one shown row asserted at the instant it was composed.

    Frozen at COMPOSE time and never re-read from the register. That is the
    whole point: `record` runs after the transport answered, which can be
    seconds or minutes after the card was built, and the register is free to
    move in between. Re-reading it there is how a ledger comes to say a person
    was told about a severity that did not exist yet when they were told.
    """
    rank = row.get("severity_rank")
    return {
        "key": str(row.get("key") or ""),
        "kind": row.get("kind"),
        "severity": row.get("severity"),
        "severity_rank": int(rank) if isinstance(rank, int) else -1,
        "state": row.get("state"),
        "first_seen": row.get("first_seen"),
        "resolved_at": row.get("resolved_at"),
        "episode": episode_of(row),
    }


def freeze(digest: dict, *, idempotency_key: str | None = None) -> dict:
    """The version of this document that a send would carry, and its hash.

    Returns the frozen block, and does NOT put it anywhere: `compose` embeds it
    and `record` reads it back. The payload is the document as composed and
    nothing is copied out of it, so the hash is over exactly the bytes a
    renderer was handed — a second copy of the rows kept here would be one more
    thing that can disagree with the card.

    `version` rather than the bare hash because a person reading a receipt
    wants to know WHICH digest, and the generation answers that question for
    the register's own history while the hash answers it for the payload.
    """
    payload = {key: value for key, value in digest.items()
               if key != FROZEN_FIELD}
    blob = _canonical(payload)
    payload_hash = hashlib.sha256(blob).hexdigest()
    generation = int(digest.get("generation") or 0)
    device = str(digest.get("device_ref") or DEVICE_FALLBACK)
    rows = [row_version(row) for row in digest.get("shown") or ()]
    return {
        "schema": FROZEN_SCHEMA,
        "generation": generation,
        "payload_hash": payload_hash,
        "payload_bytes": len(blob),
        "version": f"{generation}@{payload_hash[:12]}",
        "idempotency_key": (idempotency_key
                            or f"alert-digest-{device}-{generation}"),
        "rows": rows,
        "row_count": len(rows),
    }


def compose(store, cursor: DeliveryCursor, *, at: str, device_ref: str,
            policy_block: dict | None = None) -> dict:
    """The document a renderer turns into one card. No transport, no send.

    `shown` and `more` exist so that a card can be short without the document
    being short. A composer that returned only the top N would make the number
    of things wrong a function of how many fit — and the reader would have no
    way to tell "three" from "three hundred".

    The returned document carries its own FROZEN version: the payload hash, the
    generation, and the severity/state/episode each shown row asserted here.
    Composing is the only instant at which those are known to be what the card
    says, so it is the instant that has to write them down.
    """
    block = policy_block or policy()
    top_n = int(block.get("top_n") or 0)
    if top_n <= 0:
        raise DigestError(
            f"{CONTRACT}#alerts.digest.top_n is {top_n!r}; a card that shows "
            f"nothing is not a digest")
    found = deltas(store, cursor, at=at, policy_block=block)
    shown = found["rows"][:top_n]
    document = {
        "schema": SCHEMA,
        "device_ref": device_ref,
        "at": at,
        "baseline_at": found["baseline_at"],
        "generation": cursor.generation() + 1,
        "kinds": list(KINDS),
        "counts": found["counts"],
        "severity_counts": found["severity_counts"],
        "rows": found["rows"],
        # Open alerts the last pass could not close, with the machine-readable
        # reason. NOT deltas and never announced — see `deltas`. They are here
        # because two of the five kinds are a loss of observation, and a
        # monitoring-integrity finding cannot be built from a document that
        # does not carry them.
        "held": found["held"],
        "shown": shown,
        "shown_keys": [row["key"] for row in shown],
        "more": max(0, len(found["rows"]) - len(shown)),
        "immediate": found["immediate"],
        "standing": found["standing"],
        "backlog": found["backlog"],
        "silent_resolutions": found["silent_resolutions"],
        "note": ("`shown` is what fits on one card; `rows` is everything the "
                 "delta found and `more` is the difference. `backlog` counts "
                 "identities that were already open when this ledger started: "
                 "they are visible in the register and in `standing`, and they "
                 "are never announced as new."),
    }
    document[FROZEN_FIELD] = freeze(document)
    return document


# ---------------------------------------------------------------------------
# When to say it. Two rules, and both are policy.
# ---------------------------------------------------------------------------


def decision(digest: dict, cursor: DeliveryCursor, *, at: str,
             policy_block: dict | None = None) -> dict:
    """Whether this document is worth a message, and the key it would carry.

    Two clocks and TWO AXES. The clock decides when a worth-saying thing may be
    said; the axes decide whether there is one, and a document that reaches the
    send has to clear both of them:

      * something `immediate`, at a severity the contract lets out of band, AND
        that may occupy a line on the card, and the immediate cooldown has
        elapsed -> say it now, without waiting for the interval. A CRITICAL that
        waits an hour because the cadence is hourly is the failure the split
        exists to stop.
      * otherwise, something worth saying and the full interval has elapsed ->
        say it.
      * otherwise -> do not. A quiet hour produces no card, and that is the
        intended outcome rather than a gap in the record.

    The axes are `severity` (via `proactive_severities` / `immediate_severities`)
    and `attention` (via `may_interrupt`), and the contract is explicit that they
    are read together: severity answers "how bad is what was measured", attention
    answers "may this person be interrupted". Reading only the first is how a
    card comes to be sent that says there is nothing to report.

    Nothing is marked told here. This function decides; `record` writes.
    """
    block = policy_block or policy()
    interval = float(block.get("interval_seconds") or 0)
    if interval <= 0:
        raise DigestError(
            f"{CONTRACT}#alerts.digest.interval_seconds is {interval!r}; with no "
            f"interval every call would be a new digest")
    cooldowns = dict((_alerting().policy() or {}).get("cooldown_seconds") or {})
    # Both doors carry the attention test, and the immediate door is the one
    # that was actually firing here: measured 2026-09-24, all four of this
    # node's most recent cards arrived LESS than `interval_seconds` after the
    # previous one, so not one of them was the interval's doing. A high-severity
    # `HIGH_MEMORY_SUSTAINED` opens, is `immediate`, and preempts the hour — for
    # a row that can never reach the face. An immediate finding that cannot be
    # printed on a card must not preempt the interval either.
    # ONE table for the whole decision, not one per row. See `face_table`.
    face = face_table()
    urgent = [row for row in digest["shown"]
              if row["immediate"] and may_interrupt(row, table=face)]

    since_digest = None
    if cursor.last_digest_at():
        since_digest = _seconds_between(cursor.last_digest_at(), at)
    since_attempt = None
    if cursor.last_attempt_at():
        since_attempt = _seconds_between(cursor.last_attempt_at(), at)

    generation = cursor.generation() + 1
    key = f"alert-digest-{digest.get('device_ref') or 'device'}-{generation}"
    out = {"send": False, "why": "", "generation": generation,
           "idempotency_key": key, "immediate": [r["key"] for r in urgent],
           "interval_seconds": interval,
           "seconds_since_last_digest": since_digest,
           "seconds_since_last_attempt": since_attempt}

    if not digest["rows"]:
        out["why"] = ("nothing in the register changed since the last digest "
                      "that was told to a person")
        return out

    if urgent:
        gate = float(cooldowns.get(str(urgent[0]["severity"])) or 0)
        if since_attempt is None or since_attempt >= gate:
            out["send"] = True
            out["why"] = (f"{len(urgent)} immediate finding(s) at "
                          f"{urgent[0]['severity']}; the {gate:.0f}s cooldown "
                          f"for that severity has elapsed"
                          if since_attempt is not None else
                          f"{len(urgent)} immediate finding(s) and nothing has "
                          f"been attempted since the baseline")
            return out
        out["why"] = (f"{len(urgent)} immediate finding(s), but the last "
                      f"attempt was {since_attempt:.0f}s ago and the "
                      f"{gate:.0f}s cooldown for {urgent[0]['severity']} has "
                      f"not elapsed")
        return out

    # §27. The interval is a clock, not a reason. Something has to be worth
    # saying for the clock to produce a message, and what is worth saying at the
    # hour is narrower than what fits in the document: an OPENING or WORSENING
    # finding at a severity the contract covers. A RECOVERED row is excluded
    # even at HIGH — it is only sayable to somebody who was told the thing was
    # broken, so it rides along in a card that exists for another reason and
    # never makes one exist. A NOTICE is excluded because it is context for a
    # card somebody is reading, and hourly context on a quiet host is a robot
    # that talks to itself.
    floor = proactive_severities(block)
    proactive = [row for row in digest["rows"]
                 if row.get("kind") in _PROACTIVE_KINDS
                 and row.get("severity") in floor]
    if not proactive:
        out["why"] = (f"{len(digest['rows'])} finding(s), none at "
                      f"{'/'.join(floor)} and none immediate — a batch of "
                      f"recoveries, notices or already-told findings is not "
                      f"worth a message of its own")
        return out

    # §27's second half, and the half that was missing. The severity floor is
    # NECESSARY and not sufficient: it asks "may this cause a card" without ever
    # asking "would the card show it". A finding the renderer counts instead of
    # printing is a reason to send a card announcing that there is nothing to
    # report, so the face test decides, and the two doors above and here are the
    # same door.
    worthy = [row for row in proactive if may_interrupt(row, table=face)]
    if not worthy:
        out["why"] = (f"{len(proactive)} finding(s) at {'/'.join(floor)}, and "
                      f"none of them may occupy a line on the card — a card "
                      f"caused by a finding it cannot print arrives saying there "
                      f"is nothing to do")
        return out

    if since_digest is None or since_digest >= interval:
        out["send"] = True
        out["why"] = (f"the {interval:.0f}s interval has elapsed since the last "
                      f"delivered digest, and {len(worthy)} of "
                      f"{len(digest['rows'])} finding(s) are at "
                      f"{'/'.join(floor)} and may occupy a line on the card"
                      if since_digest is not None else
                      f"no digest has been delivered yet and {len(worthy)} of "
                      f"{len(digest['rows'])} finding(s) are at "
                      f"{'/'.join(floor)} and may occupy a line on the card")
        return out

    out["why"] = (f"{len(digest['rows'])} finding(s), {len(worthy)} of them at "
                  f"{'/'.join(floor)} and printable, none immediate, and only "
                  f"{since_digest:.0f}s of the {interval:.0f}s interval have "
                  f"elapsed")
    return out


def _shown_pairs(shown) -> list[tuple[str, str | None]]:
    """(key, kind) from either the digest's own rows or a list of keys.

    Both are accepted because both are honest callers: the delivery surface
    holds the whole document and can hand `shown` over verbatim, while a
    diagnostic that only wants to stamp one key should not have to invent a
    kind it does not have. A missing kind is stored as None rather than
    guessed — the cursor's job is to say what was told, and "told, kind
    unrecorded" is a true statement where a default would not be.
    """
    out: list[tuple[str, str | None]] = []
    for item in shown or ():
        if isinstance(item, dict):
            if item.get("key"):
                out.append((str(item["key"]), item.get("kind")))
        elif item:
            out.append((str(item), None))
    return out


def _frozen_rows(frozen) -> dict[str, dict]:
    """key -> the version of that row the payload was frozen with.

    An empty mapping when there is no frozen block, which is the one case the
    caller has to be able to tell from "the block is there and lists nothing":
    the first is an older document and is recorded honestly as such, and the
    second is a payload whose hash covers no rows at all.
    """
    if not isinstance(frozen, dict):
        return {}
    rows = frozen.get("rows")
    if not isinstance(rows, list):
        return {}
    return {str(row["key"]): row for row in rows
            if isinstance(row, dict) and row.get("key")}


def _refuse_a_different_document(frozen: dict, generation: int) -> None:
    """The version being recorded against must be the version that was frozen.

    The one disagreement here that is REFUSED rather than reported. The others
    — a row whose severity differs from the frozen table, a row the payload
    does not name, a named row the delivery did not carry — are disagreements
    about CONTENT, and the frozen table settles them: it is what the composing
    instant wrote down, so it wins and the disagreement is reported. Refusing
    those would leave a message that really was sent unrecorded, which is the
    worse of the two failures.

    A generation mismatch is not a disagreement about content. It means the
    caller is recording the delivery of one document against another, and
    accepting it would advance this cursor to a generation whose rows nobody
    composed — marking announced a set of rows that was never shown, and
    silently consuming the deltas of the generation that actually went out.
    """
    declared = frozen.get("generation")
    if declared is not None and int(declared) != int(generation):
        raise DigestError(
            f"the frozen payload is generation {declared} and the delivery is "
            f"being recorded against generation {generation}. They are two "
            f"different documents, and a receipt naming the wrong one would "
            f"advance this cursor past a version nobody was shown.")


def record(cursor: DeliveryCursor, store, *, at: str, generation: int,
           state: str, shown, reason: str | None = None,
           frozen: dict | None = None) -> dict:
    """What we know after trying. The same vocabulary as `alerting`.

    DELIVERED means the transport accepted the call — not that a person read
    it. It is the ONLY value that advances the generation and marks keys
    announced, because a delta that was composed and did not arrive has not
    been told to anybody, and marking it told would be the one lie that makes
    the next digest wrong in a direction nobody can see.

    On a failed attempt the generation does NOT move and the delta window stays
    open, so the next digest re-composes the same findings. That is not a retry
    loop: it is the same rule `alerting.record_delivery` states, and `decision`
    is what stops two attempts inside one cooldown.

    THE VALUES RECORDED ARE THE FROZEN ONES
    ---------------------------------------
    This runs AFTER the transport answered, which may be minutes after the card
    was composed, and the register is free to move in between. Reading
    `severity` off the register here was a race with a specific symptom: the
    card said MEDIUM, the detector escalated to HIGH before the receipt was
    written, and the cursor recorded that HIGH had been announced. The person
    was told MEDIUM and never told about HIGH — the escalation is compared
    against the recorded severity, so it had already been swallowed — and the
    ledger asserted the opposite in the one place nobody re-reads.

    So the severity, the state and the EPISODE are taken from the frozen
    payload the card carried, and the register's `last_notified_severity` is
    set to what the person was actually shown. Whatever the register does next
    becomes a delta like any other: an escalation stays pending, a resolution
    becomes RECOVERED, a reopen becomes REOPENED. §4's requirement stated as
    one line — the receipt names the version, and the world is free to move on
    from it.

    A frozen block that contradicts the rows it arrived with does not win the
    argument and does not become an exception either: the frozen half is what
    the composing instant wrote down, so it is what gets recorded, and the keys
    that disagreed are named in the receipt (`disagreed`, `unnamed`). A
    document with no frozen block at all is recorded the old way, with the
    cursor saying so — an older document is not a lying one. The one thing
    refused is a frozen block naming a generation other than the one being
    recorded, because that is not a disagreement about content but a delivery
    being attached to a document nobody composed.
    """
    if state not in ("DELIVERED", "FAILED", "UNSUPPORTED", "UNKNOWN"):
        raise DigestError(
            f"{state!r} is not one of DELIVERED, FAILED, UNSUPPORTED, UNKNOWN — "
            f"an outcome nobody can name is an outcome that will be read as a "
            f"delivery")
    if state != "DELIVERED":
        summary = cursor.commit(
            last_attempt_at=at, last_delivery=state,
            delivery_failure=reason or f"delivery answered {state}")
        # The SAME shape as the delivered branch. The caller's question does
        # not change because the answer was no: what happened, and may the
        # window close. A dictionary with `delivered` in it only on success
        # makes `if out["delivered"]` a KeyError at exactly the moment
        # somebody is trying to find out whether anybody was told.
        return {"delivered": False, "generation": cursor.generation(),
                "announced_keys": 0, "rows_stamped": 0,
                "gone_from_register": [], "superseded": [], "cursor": summary,
                "payload_version": None, "payload_hash": None,
                "failure": summary.get("delivery_failure")}

    pairs = _shown_pairs(shown)
    versions = _frozen_rows(frozen)
    payload_version = None
    payload_hash = None
    if versions:
        _refuse_a_different_document(frozen, generation)
        payload_version = frozen.get("version")
        payload_hash = frozen.get("payload_hash")

    # The rows as the caller handed them over, for the one comparison the
    # frozen table cannot make on its own: does the row it was handed agree
    # with the row it froze? It cannot happen on the real path — the frozen
    # block and the shown rows are two readings of one document — so a
    # disagreement means a caller edited or rebuilt one of the halves, and the
    # frozen half wins while the key is named in the receipt.
    handed = {str(row["key"]): row for row in (shown or ())
              if isinstance(row, dict) and row.get("key")}
    contradicted = []

    keys = cursor.keys()
    alerts = store.alerts()
    stamped = 0
    missing = []
    superseded = []
    unnamed = []
    for key, kind in pairs:
        version = versions.get(key)
        if versions and version is None:
            # A row the frozen payload does not name. It was handed over as
            # shown, and the payload — which is the authority on what was
            # frozen — has nothing to say about its severity. Recorded with
            # nulls, exactly as a document with no frozen block at all is, and
            # named in the receipt so that "the card carried a row nobody
            # composed" is visible rather than assumed away.
            unnamed.append(key)
        episode = version.get("episode") if version else None
        told_severity = version.get("severity") if version else None
        told_state = version.get("state") if version else None
        if version is not None and version.get("kind"):
            kind = version["kind"]
        if version is not None:
            row = handed.get(key) or {}
            if any(row.get(field) != version.get(field)
                   for field in ("severity", "state")):
                contradicted.append(key)

        # AN OLDER RECEIPT DOES NOT OVERWRITE A NEWER ONE. Two deliveries can
        # be in flight at once — a slow send that finally answered after the
        # next digest had already been composed, delivered and recorded, or an
        # attempt that timed out while the provider had in fact accepted it.
        # The late answer is true about the version it names and false about
        # the cursor's PRESENT: applying it would move `severity` and `state`
        # backwards, un-recording an escalation or a recovery that had already
        # been announced. The delta that separated the two versions would then
        # be announced a second time, because the comparison it is found by
        # reads the cursor.
        #
        # The cursor's generation is the ORDER the versions were composed in,
        # not the order the transports happened to answer in. An ACK below it
        # is therefore superseded, whatever episode it names and whatever it
        # says — the newer entry already describes what the person last heard.
        # An ACK at the SAME generation still applies, which is what makes a
        # duplicate retry of one document idempotent rather than a conflict.
        held = keys.get(key)
        if (isinstance(held, dict)
                and int(held.get("generation") or 0) > int(generation)):
            superseded.append(key)
            continue

        alert = alerts.get(key)
        if not versions and alert is not None:
            # No frozen block at all: the register is read here, after the
            # fact, and the cursor entry records that this is where the value
            # came from. Note that this is the ONLY path on which that happens
            # — a key the frozen payload did not name is not read from the
            # register either, because reading it would put a post-send value
            # on a row that was composed with something else.
            told_severity = alert.get("severity")
            told_state = alert.get("state")
        if alert is None:
            # The register no longer holds it: the detector resolved it in the
            # seconds between composing and delivering. Recorded rather than
            # skipped, and recorded IN THE CURSOR rather than only in the return
            # value — a key that was on the card was told to somebody, and this
            # docstring's own claim is that a cursor which forgot it would
            # announce the next identity reusing those fields as if it were new.
            # It was doing exactly that: the caller was handed `missing` and the
            # ledger still had no memory of the key. The fields that cannot be
            # known here are null, which is true, rather than copied from
            # whatever the register holds now, which would not be — and when
            # the payload was frozen they are known, so they are carried.
            missing.append(key)
            keys[key] = {"announced": True, "at": at, "kind": kind,
                         "severity": told_severity, "state": told_state,
                         "episode": episode,
                         "frozen": version is not None,
                         "payload_version": payload_version,
                         "generation": int(generation)}
            continue
        keys[key] = {
            "announced": True,
            "at": at,
            "kind": kind,
            "severity": told_severity,
            "state": told_state,
            "episode": episode,
            "frozen": version is not None,
            "payload_version": payload_version,
            "generation": int(generation),
        }
        # The register's OWN fields, set to what they have always meant: "this
        # was announced". `alert list` and `alert inspect` read them, and a
        # person who has been told five digests about a disk must not find
        # `last_notified_at: null` on it. `pending` is deliberately NOT touched:
        # that intent belongs to the engine's own delivery hop, and a second
        # surface clearing it would be one surface editing another's ledger.
        #
        # `last_notified_severity` is the FROZEN one. It is read by a person
        # asking "what was I told about this", so it has to be the severity
        # that was on the card and not the severity the register happens to
        # hold by the time the transport answered.
        alert["last_notified_at"] = at
        alert["last_notified_severity"] = told_severity
        alert["last_delivery"] = "DELIVERED"
        alert["delivery_failure"] = None
        alert["last_digest_generation"] = int(generation)
        stamped += 1
    store.commit(alerts)
    receipt = None
    if payload_version is not None:
        receipt = {"version": payload_version, "payload_hash": payload_hash,
                   "generation": int(generation), "at": at,
                   "rows": len(pairs), "superseded": sorted(superseded),
                   "unnamed": sorted(unnamed),
                   "disagreed": sorted(contradicted)}
    summary = cursor.commit(generation=int(generation), last_digest_at=at,
                            last_attempt_at=at, last_delivery="DELIVERED",
                            delivery_failure=None, keys=keys,
                            last_payload=receipt)
    return {"delivered": True, "generation": int(generation),
            "announced_keys": len(pairs) - len(missing),
            "rows_stamped": stamped,
            "gone_from_register": missing, "superseded": sorted(superseded),
            "unnamed_rows": sorted(unnamed), "disagreed_rows": sorted(contradicted),
            "payload_version": payload_version, "payload_hash": payload_hash,
            "cursor": summary}


def main(argv: list[str] | None = None) -> int:
    """Read one register and one cursor. A diagnostic, not the product surface."""
    import argparse
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--state", required=True, help="the alerts state file")
    parser.add_argument("--at", required=True, help="the instant to compose as of")
    parser.add_argument("--device", default=DEVICE_FALLBACK)
    args = parser.parse_args(argv)
    store = _alerting().AlertStore(args.state)
    cursor = DeliveryCursor(cursor_path(args.state))
    json.dump(compose(store, cursor, at=args.at, device_ref=args.device),
              sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
