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
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

CONTRACT = _HERE / "notification-contract.yaml"
SCHEMA = "serveros/alert-digest.v1"
CURSOR_SCHEMA = "serveros/alert-delivery.v1"

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
    """
    declared = (block or policy()).get("proactive_severities")
    if not isinstance(declared, (list, tuple)) or not declared:
        raise DigestError(
            f"{CONTRACT}#alerts.digest.proactive_severities is {declared!r}. "
            f"Without it the cadence has no floor and every hour with any row "
            f"at all becomes a card, which is the shape this key exists to "
            f"prevent")
    return tuple(str(word) for word in declared)


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
            "known_keys": len(keys),
            "announced_keys": sum(1 for row in keys.values()
                                  if isinstance(row, dict) and row.get("announced")),
        }

    # -- the one write ----------------------------------------------------

    def commit(self, *, baseline_at=None, generation=None, last_digest_at=None,
               last_attempt_at=None, last_delivery=None, delivery_failure=None,
               keys=None) -> dict:
        """The whole document, in one atomic write.

        Whole-document rather than per-record for the same reason the alert
        store is: recording a delivery advances the generation, the clock and
        every announced key in the same instant, and a row-by-row write would
        have a window in which a key is marked announced in a generation that
        was never sent.
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


def _row_of(alert: dict, *, at: str, ranks: dict, words: dict) -> dict:
    duration = _seconds_between(alert.get("first_seen"), at)
    severity = str(alert.get("severity") or "")
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
        "counts": {kind: sum(1 for r in rows if r["kind"] == kind) for kind in KINDS},
        "severity_counts": {sev: sum(1 for r in rows if r["severity"] == sev)
                            for sev in ("high", "medium", "low")},
        "immediate": [r["key"] for r in rows if r["immediate"]],
        "standing": standing,
        "backlog": backlog,
        "silent_resolutions": silent_resolutions,
    }


def compose(store, cursor: DeliveryCursor, *, at: str, device_ref: str,
            policy_block: dict | None = None) -> dict:
    """The document a renderer turns into one card. No transport, no send.

    `shown` and `more` exist so that a card can be short without the document
    being short. A composer that returned only the top N would make the number
    of things wrong a function of how many fit — and the reader would have no
    way to tell "three" from "three hundred".
    """
    block = policy_block or policy()
    top_n = int(block.get("top_n") or 0)
    if top_n <= 0:
        raise DigestError(
            f"{CONTRACT}#alerts.digest.top_n is {top_n!r}; a card that shows "
            f"nothing is not a digest")
    found = deltas(store, cursor, at=at, policy_block=block)
    shown = found["rows"][:top_n]
    return {
        "schema": SCHEMA,
        "device_ref": device_ref,
        "at": at,
        "baseline_at": found["baseline_at"],
        "generation": cursor.generation() + 1,
        "kinds": list(KINDS),
        "counts": found["counts"],
        "severity_counts": found["severity_counts"],
        "rows": found["rows"],
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


# ---------------------------------------------------------------------------
# When to say it. Two rules, and both are policy.
# ---------------------------------------------------------------------------


def decision(digest: dict, cursor: DeliveryCursor, *, at: str,
             policy_block: dict | None = None) -> dict:
    """Whether this document is worth a message, and the key it would carry.

    Two clocks, and the split is §Q2's severity filter expressed as one rule:

      * something `immediate` and the immediate cooldown has elapsed -> say it
        now, without waiting for the interval. A CRITICAL that waits an hour
        because the cadence is hourly is the failure the split exists to stop.
      * otherwise, anything at all and the full interval has elapsed -> say it.
      * otherwise -> do not. A quiet hour produces no card, and that is the
        intended outcome rather than a gap in the record.

    Nothing is marked told here. This function decides; `record` writes.
    """
    block = policy_block or policy()
    interval = float(block.get("interval_seconds") or 0)
    if interval <= 0:
        raise DigestError(
            f"{CONTRACT}#alerts.digest.interval_seconds is {interval!r}; with no "
            f"interval every call would be a new digest")
    cooldowns = dict((_alerting().policy() or {}).get("cooldown_seconds") or {})
    urgent = [row for row in digest["shown"] if row["immediate"]]

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
    worthy = [row for row in digest["rows"]
              if row.get("kind") in _PROACTIVE_KINDS
              and row.get("severity") in floor]
    if not worthy:
        out["why"] = (f"{len(digest['rows'])} finding(s), none at "
                      f"{'/'.join(floor)} and none immediate — a batch of "
                      f"recoveries, notices or already-told findings is not "
                      f"worth a message of its own")
        return out

    if since_digest is None or since_digest >= interval:
        out["send"] = True
        out["why"] = (f"the {interval:.0f}s interval has elapsed since the last "
                      f"delivered digest, and {len(worthy)} of "
                      f"{len(digest['rows'])} finding(s) are at "
                      f"{'/'.join(floor)}"
                      if since_digest is not None else
                      f"no digest has been delivered yet and {len(worthy)} of "
                      f"{len(digest['rows'])} finding(s) are at "
                      f"{'/'.join(floor)}")
        return out

    out["why"] = (f"{len(digest['rows'])} finding(s), {len(worthy)} of them at "
                  f"{'/'.join(floor)}, none immediate, and only "
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


def record(cursor: DeliveryCursor, store, *, at: str, generation: int,
           state: str, shown, reason: str | None = None) -> dict:
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
                "gone_from_register": [], "cursor": summary,
                "failure": summary.get("delivery_failure")}

    pairs = _shown_pairs(shown)
    keys = cursor.keys()
    alerts = store.alerts()
    stamped = 0
    missing = []
    for key, kind in pairs:
        alert = alerts.get(key)
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
            # whatever the register holds now, which would not be.
            missing.append(key)
            keys[key] = {"announced": True, "at": at, "kind": kind,
                         "severity": None, "state": None,
                         "generation": int(generation)}
            continue
        keys[key] = {
            "announced": True,
            "at": at,
            "kind": kind,
            "severity": alert.get("severity"),
            "state": alert.get("state"),
            "generation": int(generation),
        }
        # The register's OWN fields, set to what they have always meant: "this
        # was announced". `alert list` and `alert inspect` read them, and a
        # person who has been told five digests about a disk must not find
        # `last_notified_at: null` on it. `pending` is deliberately NOT touched:
        # that intent belongs to the engine's own delivery hop, and a second
        # surface clearing it would be one surface editing another's ledger.
        alert["last_notified_at"] = at
        alert["last_notified_severity"] = alert.get("severity")
        alert["last_delivery"] = "DELIVERED"
        alert["delivery_failure"] = None
        alert["last_digest_generation"] = int(generation)
        stamped += 1
    store.commit(alerts)
    summary = cursor.commit(generation=int(generation), last_digest_at=at,
                            last_attempt_at=at, last_delivery="DELIVERED",
                            delivery_failure=None, keys=keys)
    return {"delivered": True, "generation": int(generation),
            "announced_keys": len(pairs) - len(missing), "rows_stamped": stamped,
            "gone_from_register": missing, "cursor": summary}


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
