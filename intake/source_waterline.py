#!/usr/bin/env python3
"""B2's two waterlines per source, as intervals with the gaps named.

`docs/COLLABORATION-CONTRACT.md` §6 asks for two waterlines on every source —
how far the CAPTURE has brought material in, and how far UNDERSTANDING has
accounted for it — and states one rule that no single number can satisfy:

    任何中间 gap 都不能被最大位置掩盖。

That rule is why this module is not one integer per source. Read a source
holding positions 0–4 and 9–13. It reaches 13. It is also missing four positions
in the middle, and every scalar summary in existence — the maximum, the count,
`latest_create_time` — reports that capture as complete up to 13. The gap is not
smaller than the maximum; it is invisible in it. So both waterlines here are
**lists of runs plus the gaps between them**, and the comparison reports
separately the four things a lag number would merge:

  * `lag` — captured positions understanding has not accounted for;
  * `unread_capture_gaps` — interior positions between the ones the capture
    holds. Reported even when `lag` is EMPTY, because a source whose every
    captured position is understood can still be a source with a hole in it,
    and that hole is exactly what a reader of the lag alone will never see.
    Whether a hole is missing MATERIAL is a separate question, decided by the
    capture's own account and not by the labels: see `_gaps_corroborated`;
  * `beyond_source` — understanding claiming a reach the capture does not hold.
    Reported rather than clamped: clamping would make a card citing 0–999
    against a capture of 0–13 look current;
  * `tail` — whether the extent is a boundary the source ended at or one the
    collector stopped at. An open tail means the material past the extent is
    UNKNOWN, which is not the word EMPTY and must never be spelled "no lag".

Nothing here reads a tree. Both sides come from readers that already exist:
`capture_coverage` decides what each capture object holds, and
`understanding_delta` reads the messages files and the cards. `capture_waterline`
takes a CALLABLE for the runs so a document source never has a messages file
looked for, and copies `capture_coverage`'s component verdict rather than
re-deriving pagination completeness here — two opinions about one manifest is
how the drift this work exists to remove gets started.

A card's cited reach is understanding's, at card granularity. A committed
semantic reading lives inside the same card and reaches at least as far, so the
finer grain is `understanding_semantic`'s and the two meet at the card; this
module does not invent a second measure to look more precise.
"""
from __future__ import annotations

from pathlib import Path

SCHEMA = "serveros/source-waterline.v1"

#: What a capture object is, for the purpose of a waterline. `POSITIONAL` has an
#: order to be behind on; `DOCUMENT` has a digest and no positions at all, so a
#: positional comparison against it is not a comparison that can be made.
CAPTURE_KINDS = ("POSITIONAL", "DOCUMENT", "UNREADABLE")

#: Worst first. A capture can be several of these at once, and the state is the
#: worst while `why` names all of them — a summary that reported only the first
#: would let the others go unmentioned.
#:
#: `GAPPED`, `DUPLICATED` and `LABELS_NOT_CONTIGUOUS` lead `INCOMPLETE`
#: deliberately, and not because a hole is more serious than a fetch that
#: stopped early. It is because a hole is invisible in every other record:
#: `capture_coverage` already reports `messages` INCOMPLETE on the object, so a
#: state naming that leaves the hole named nowhere, while a state naming the
#: hole leaves INCOMPLETE readable in `declared.components` and in the tail
#: sentence. The ordering decides which fact is at the head, and the one that
#: has nowhere else to appear goes there.
#:
#: `GAPPED` and `LABELS_NOT_CONTIGUOUS` are two answers to what a hole is, and
#: they are not the same answer — see `_gaps_corroborated`.
CAPTURE_STATES = ("UNREADABLE", "GAPPED", "DUPLICATED", "LABELS_NOT_CONTIGUOUS",
                  "INCOMPLETE", "UNDECIDABLE", "COMPLETE")

#: Worst first, and what a card citing a source does about its reach.
UNDERSTOOD_STATES = ("UNMEASURABLE", "GAPPED", "COMPLETE", "NO_REACH", "NONE")

#: The messages-file states in which no extent can be stated. A file that is not
#: there is in this set: "no file" and "no messages" are not distinguishable
#: here, and the honest answer for the pair is that the extent is unknown rather
#: than that it is zero.
_UNREADABLE_RUN_STATES = ("NO_MESSAGES_FILE", "UNREADABLE", "MALFORMED",
                          "POSITIONS_INCOMPLETE")

#: Worst first, except that `NOT_POSITIONAL` is not a severity — it says the
#: positional comparison does not apply to this source at all. It is in the same
#: field so a reader of `state` reads one field, and its place in this tuple is
#: not a claim about how bad it is.
#:
#: `GAPPED`, `DUPLICATED` and `LABELS_NOT_CONTIGUOUS` are the capture's own
#: words, reused here rather than restated: a defect in the capture has to be
#: resolved before the comparison between the two waterlines means anything, so
#: the headline is that defect and not a lag computed across it.
COMPARISON_STATES = ("UNREADABLE", "UNRESOLVED", "INCONSISTENT", "GAPPED",
                     "DUPLICATED", "LABELS_NOT_CONTIGUOUS", "BEHIND",
                     "TAIL_UNKNOWN", "NOT_POSITIONAL", "UP_TO_DATE")

CAPTURE_PRODUCED = {
    "source": "the capture object's own `path`, copied from `capture_coverage`. "
              "Copied and not rebuilt: a source key is what every other record "
              "joins on",
    "kind": "one of CAPTURE_KINDS, decided by the capture object's own `shape`",
    "state": "one of CAPTURE_STATES, worst first",
    "runs": "the contiguous runs of `message_position` the messages file holds — "
            "from `understanding_delta.read_position_runs`, the one reader of "
            "that file. Empty for a document, which has no positions",
    "gaps": "the interior positions between those runs — positions the capture "
            "does NOT hold, which is why they are part of the waterline and not "
            "a caveat beside it. What a hole MEANS is not decided here: the "
            "state says `GAPPED` only when a record says material is missing, "
            "and `LABELS_NOT_CONTIGUOUS` when none does",
    "duplicates": "positions appearing more than once. A duplicate makes a count "
                  "and a maximum disagree with the file, so it is named",
    "low": "the oldest captured position, or null",
    "high": "the newest captured position, or null. Never the waterline on its "
            "own — see the module docstring",
    "tail": "CLOSED, OPEN, or null for a source that has no positions. CLOSED "
            "only when the component that carries this source's own reach says "
            "COMPLETE and the positions are readable; OPEN otherwise, which "
            "means the material past `high` is unknown rather than absent",
    "declared": "the collector's own account, copied: every component's state, "
                "and the facts of the one component that decides this waterline. "
                "Copied and never summarised into the state — the collector's "
                "account of its own work is evidence, not a measurement",
    "why": "every reason the state is not COMPLETE, in words, and empty when it is",
}

UNDERSTOOD_PRODUCED = {
    "source": "the source these reaches are against, from the measurement rows",
    "state": "one of UNDERSTOOD_STATES, worst first",
    "runs": "the union of the cards' cited reaches, as contiguous runs",
    "gaps": "interior positions no card's reach covers, between ones it does",
    "spans": "the cited reaches themselves, so a run can be traced to the cards "
             "that produced it",
    "why": "one sentence per reason the state is not COMPLETE",
}

COMPARISON_PRODUCED = {
    "source": "the capture object's own key, copied from the capture side. On an "
              "`UNRESOLVED` row there is no capture object and therefore no such "
              "key, so it is the cited path AS THE CARD WROTE IT — records-root-"
              "relative, the other coordinate system. The two spellings are "
              "never both possible for one source, and `capture: null` is what "
              "tells them apart",
    "state": "one of COMPARISON_STATES, worst first",
    "capture": "the capture waterline, copied whole — or null when no capture "
               "object holds this source at all, which is what `UNRESOLVED` "
               "names. Not summarised: the comparison's whole point is that a "
               "reader can still see the runs",
    "understood": "the understanding waterline, copied whole, for the same reason",
    "lag": "captured runs understanding has not accounted for, as runs. Present "
           "even when the two extents are equal, because a middle position can "
           "be unaccounted for while both ends agree",
    "unread_capture_gaps": "the interior positions the capture does not hold, "
                           "copied from the capture side. Reported even when "
                           "`lag` is empty: this is the fact a reader of a lag "
                           "scalar can never see. Read `capture.state` beside "
                           "it for whether the records say material is missing",
    "beyond_source": "understanding runs that extend past the capture's extent. "
                     "Reported rather than clamped",
    "why": "one sentence per fact about this source, in the order a person "
           "should read them",
}

WATERLINES_PRODUCED = {
    "schema": "this module's own schema string",
    "root": "the records root these waterlines were taken against",
    "sources": "one comparison per source: every capture object, plus every "
               "source a card cites that no capture object holds. The second "
               "half is not optional — a waterline that listed only what the "
               "capture tree holds would report a card citing a missing source "
               "as nothing at all",
}


class Refused(RuntimeError):
    """This module will not answer. Raised for a defect in the CALLER."""


def no_unsourced(mapping: dict, table: dict, where: str) -> dict:
    """Refuse to emit a key whose value the caller cannot name a source for."""
    unexpected = sorted(set(mapping) - set(table))
    if unexpected:
        raise Refused(
            f"this module would emit "
            f"{', '.join(f'`{where}.{key}`' for key in unexpected)}, which the "
            f"producer table does not name a source for; add the producer or "
            f"drop the field")
    return mapping


def _worst(states, order: tuple) -> str:
    """The worst of `states`, by the order the caller declares."""
    return min(states, key=order.index)


def _gaps_corroborated(facts: dict) -> str | None:
    """Why the records say the source holds material the capture does not.

    A hole in the label sequence is a measurement; missing material is a
    conclusion, and the two are not the same. Measured on the live tree on
    2026-09-23, all three sources whose labels are not contiguous agreed with
    themselves the other way: `msg_count` and `remote_unique_message_count` were
    equal and equal to the number of positions held, and `pagination_complete`
    was true. Read the nanjing-office chat: 4956 positions held, labels 0–5379,
    so 424 labels are absent — and the collector's own account says it wrote
    4956 and that the source holds 4956. Both readings fit that evidence, and
    nothing on this tree chooses between them.

    So nothing here decides it either. A hole is called missing material only
    when a record says so, which is what this returns: the reason if one does,
    and `None` if none does. `LABELS_NOT_CONTIGUOUS` is the answer for `None`,
    and it is not the weaker version of `GAPPED` — it is the answer to a
    different question, and reporting it as `GAPPED` would send a person looking
    for messages that may never have existed.
    """
    complete = facts.get("pagination_complete")
    written = facts.get("msg_count")
    remote = facts.get("remote_unique_message_count")
    if complete is False:
        return ("the collector recorded `pagination_complete: false`")
    if isinstance(remote, int) and isinstance(written, int) and remote > written:
        return (f"the collector's independent count ({remote}) is larger than "
                f"the {written} messages it wrote")
    return None


# --------------------------------------------------------------------------
# interval algebra, which is what the rest of this file is about
# --------------------------------------------------------------------------

def runs_of(spans) -> list[list[int]]:
    """The union of spans, as contiguous runs, oldest first.

    Union and not concatenation: two cards that both cite 0–9 cover 0–9 once,
    and a waterline that counted them twice would report understanding further
    along than it is.
    """
    runs: list[list[int]] = []
    for low, high in sorted((int(a), int(b)) for a, b in spans
                            if a is not None and b is not None):
        if high < low:
            continue
        if runs and low <= runs[-1][1] + 1:
            runs[-1][1] = max(runs[-1][1], high)
        else:
            runs.append([low, high])
    return runs


def gaps_of(runs: list[list[int]]) -> list[list[int]]:
    """The interior positions between runs. Nothing outside the extent counts.

    A run that starts above zero is where the captured conversation begins, not
    a hole, and the same at the end: only a position missing BETWEEN two
    captured ones is a fact about the capture.
    """
    return [[runs[index][1] + 1, runs[index + 1][0] - 1]
            for index in range(len(runs) - 1)
            if runs[index + 1][0] > runs[index][1] + 1]


def below(runs: list[list[int]], covered: list[list[int]]) -> list[list[int]]:
    """The parts of `runs` that `covered` does not reach, as runs.

    Set difference over intervals, which is what turns two waterlines into a lag
    that survives a gap: `max - max` cannot be expressed here at all, because a
    position in the middle is not on the way from one extent to the other, and
    an implementation that has only the two extents cannot report a middle
    position as unaccounted for however it is written.

    `covered` is normalized first: the walk below steps through it once in
    order, which holds for runs and not for an arbitrary list of spans.
    """
    out: list[list[int]] = []
    for low, high in runs:
        cursor = low
        for covered_low, covered_high in runs_of(covered):
            if covered_high < cursor:
                continue
            if covered_low > high:
                break
            if covered_low > cursor:
                out.append([cursor, min(covered_low - 1, high)])
            cursor = max(cursor, covered_high + 1)
            if cursor > high:
                break
        if cursor <= high:
            out.append([cursor, high])
    return out


def above(runs: list[list[int]], ceiling: int | None) -> list[list[int]]:
    """The parts of `runs` past `ceiling` — a reach the other side does not hold."""
    if ceiling is None:
        return [list(run) for run in runs]
    return [[max(low, ceiling + 1), high] for low, high in runs if high > ceiling]


#: How many runs a RENDERED line shows. The live nanjing-office capture holds
#: 206 runs under labels 0–5379, and a card carrying 206 of them is a card
#: nobody reads. The limit is a property of the rendering and of nothing else:
#: the JSON keeps every run, and a truncated line says how many it elided, so a
#: reader is never shown a short list that looks like a complete one.
RUNS_RENDERED = 12


def _runs_text(runs, limit: int | None = RUNS_RENDERED) -> str:
    if not runs:
        return "—"
    shown = runs if limit is None else runs[:limit]
    text = " ".join(str(low) if low == high else f"{low}–{high}"
                    for low, high in shown)
    if limit is not None and len(runs) > limit:
        text += f" … 另 {len(runs) - limit} 段（本条 JSON 里有全部 {len(runs)} 段）"
    return text


# --------------------------------------------------------------------------
# the capture side
# --------------------------------------------------------------------------

def _facts_of(component) -> dict:
    """A component's own `facts`, copied. Missing component copies as empty."""
    if not isinstance(component, dict):
        return {}
    facts = component.get("facts")
    return dict(facts) if isinstance(facts, dict) else {}


def capture_waterline(object: dict, runs_of_source) -> dict:
    """What has been captured for one source, with its gaps named.

    `object` is a `capture_coverage` record. `runs_of_source` is a CALLABLE
    returning `understanding_delta.read_position_runs` of that object's messages
    file; it is called only for a positional source, so a document capture never
    has a messages file looked for that it was never going to have. Both are
    passed in because this module reads no tree and holds no second opinion
    about what a source holds.
    """
    shape = object.get("shape")
    components = (object.get("coverage") or {}).get("components") or {}
    kind = ("POSITIONAL" if shape == "chat_capture" else
            "DOCUMENT" if shape == "document_capture" else "UNREADABLE")
    deciding = "messages" if kind == "POSITIONAL" else (
        "body" if kind == "DOCUMENT" else None)
    component = components.get(deciding) if deciding else None
    declared = {
        "components": {name: (part or {}).get("state")
                       for name, part in sorted(components.items())},
        "facts": _facts_of(component),
    }

    why: list[str] = []
    states: list[str] = []
    runs: dict = {"state": "NOT_POSITIONAL", "runs": [], "gaps": [],
                  "duplicates": [], "low": None, "high": None, "why": ""}
    # The tail is about the END of the extent, which is a different question from
    # whether the extent has a hole in it: a middle gap leaves both ends known,
    # so `GAPPED` does not open the tail and `COMPLETE` is not the only closed
    # one. It is decided here, where the component's own verdict is in hand,
    # rather than from the combined state after the fact.
    closed = False
    tail_why = ""

    if kind == "UNREADABLE":
        states.append("UNREADABLE")
        why.append(
            f"this capture object is `{shape}`, not a chat or a document "
            f"capture, so what it holds cannot be stated: "
            f"{object.get('why') or 'the manifest carries no shape this module reads'}")
    elif kind == "DOCUMENT":
        # A document has no positions and no pagination. Its currency is the
        # digest its body was recorded under, and that comparison belongs to
        # `understanding_delta`'s DIGEST measurement. What is said here is
        # whether the body landed, which `capture_coverage` has already decided.
        body = (component or {}).get("state")
        states.append(body if body in CAPTURE_STATES else "UNDECIDABLE")
        why.extend((component or {}).get("why") or [])
        if body not in CAPTURE_STATES:
            why.append(
                "this manifest has no `body` component state, so whether the "
                "body landed is not recorded on it")
    else:
        runs = runs_of_source()
        readable = runs["state"] not in _UNREADABLE_RUN_STATES
        messages = (component or {}).get("state")
        if not readable:
            states.append("UNREADABLE")
            why.append(
                f"the messages file is {runs['state'].lower()}: "
                f"{runs['why'] or 'its positions could not be read'}. How far "
                f"this capture reaches is therefore not a number this module can "
                f"state")
        if runs["gaps"]:
            # A hole is measured; what it means is decided by the records. See
            # `_gaps_corroborated` — this is the one place the two readings are
            # told apart, and neither is called the other's approximation.
            where = ", ".join(f"{low}–{high}" for low, high in runs["gaps"])
            corroborated = _gaps_corroborated(declared["facts"])
            held = sum(high - low + 1 for low, high in runs["runs"])
            if corroborated:
                states.append("GAPPED")
                why.append(
                    f"the capture is missing {where} between positions it "
                    f"holds, and the records say so: {corroborated}. This is a "
                    f"hole in the evidence and not a lag — nothing is behind on "
                    f"it, because there is nothing there to be behind on")
            else:
                states.append("LABELS_NOT_CONTIGUOUS")
                account = []
                if declared["facts"].get("msg_count") is not None:
                    account.append(
                        f"it wrote {declared['facts']['msg_count']} message(s)")
                if declared["facts"].get("remote_unique_message_count") is not None:
                    account.append(
                        f"the source holds "
                        f"{declared['facts']['remote_unique_message_count']}")
                if declared["facts"].get("pagination_complete") is True:
                    account.append("the fetch finished")
                why.append(
                    f"the capture holds {held} position(s) and the labels it "
                    f"holds are not contiguous: {where} fall between ones it "
                    f"has. Nothing on record says material is missing — the "
                    f"collector's own account is that "
                    + (", and ".join(account) if account else
                       "nothing on it records how far the fetch got")
                    + " — so this module states the hole and does not read it. "
                      "Either the labels skip by construction or messages are "
                      "absent, and the records do not choose; a person who needs "
                      "to know should open the source at one of those positions")
        if runs["duplicates"]:
            states.append("DUPLICATED")
            why.append(
                f"{len(runs['duplicates'])} position(s) appear more than once "
                f"({', '.join(str(p) for p in runs['duplicates'][:8])}"
                f"{'…' if len(runs['duplicates']) > 8 else ''}); a count and an "
                f"extent then disagree with the file")
        # The component that carries this source's own reach, copied rather than
        # re-derived from `pagination_complete` here: two readers of one manifest
        # drift, and the one that drifts is the one nobody feeds.
        if messages in ("INCOMPLETE", "UNDECIDABLE"):
            states.append(messages)
            why.extend((component or {}).get("why") or [])
        elif messages is None:
            states.append("UNDECIDABLE")
            why.append(
                "this manifest has no `messages` component state, so whether the "
                "fetch finished is not recorded on it")
        closed = readable and messages == "COMPLETE"
        if not closed:
            tail_why = (
                "no position is readable, so where this source ends cannot be "
                "stated, and nothing past it can be called absent"
                if not readable else
                f"the capture's own `messages` component is "
                f"{messages or 'unstated'}, so the extent ({runs['high']}) is "
                f"where the collector stopped and not where the source ended: "
                f"everything past it is UNKNOWN, and `lag` here is a lower bound "
                f"on what is unaccounted for rather than a total")

    others = [name for name, state in declared["components"].items()
              if name != deciding and state not in ("COMPLETE", None)]
    if others:
        why.append(
            "outside this waterline, the capture object records "
            + ", ".join(f"`{name}` {declared['components'][name]}" for name in others)
            + ". Those components are not the source's own reach, so they do not "
              "decide whether a position is behind — they are named so that a "
              "reader is not left thinking a partial capture is a settled one")

    state = _worst(states or ["COMPLETE"], CAPTURE_STATES)
    if tail_why:
        why.append(tail_why)

    return no_unsourced({
        "source": object.get("path"),
        "kind": kind,
        "state": state,
        "runs": runs["runs"],
        "gaps": runs["gaps"],
        "duplicates": runs["duplicates"],
        "low": runs["low"],
        "high": runs["high"],
        "tail": None if kind != "POSITIONAL" else ("CLOSED" if closed else "OPEN"),
        "declared": declared,
        "why": why,
    }, CAPTURE_PRODUCED, "capture")


# --------------------------------------------------------------------------
# the understanding side
# --------------------------------------------------------------------------

def understood_waterline(source: str, kind: str, rows: list[dict]) -> dict:
    """What understanding has accounted for on one source, as runs.

    `rows` are `understanding_delta`'s measurement rows for this source — that
    reader's own account of each citation, copied rather than re-derived. A
    `POSITION` row is a reach a card claims; a `WHOLE_FILE` row with an
    `unresolved_why` is a reach that was named and could not be compared; a
    `DIGEST` row or a `WHOLE_FILE` row without one is a citation that states no
    reach at all. Those are three different facts and are not merged: a reach
    that could not be read is not a reach that does not exist.
    """
    spans: list[tuple] = []
    unread = no_reach = 0
    for row in rows:
        if row.get("kind") == "POSITION":
            spans.append((row.get("applied_low"), row.get("applied")))
        elif row.get("unresolved_why"):
            unread += 1
        else:
            no_reach += 1
    if kind == "DOCUMENT" and spans:
        # A document has no positions, so a POSITION row against one is a defect
        # upstream rather than a reach here. Reporting a positional reach for a
        # source that has no positions would be the comparison nobody made.
        raise Refused(
            f"`{source}` is a document capture and a measurement row claimed a "
            f"position in it: two coordinate systems have been confused, and "
            f"this module will not report a reach it cannot have read")

    runs = runs_of(spans)
    gaps = gaps_of(runs)
    why: list[str] = []
    states: list[str] = []
    if unread:
        states.append("UNMEASURABLE")
        why.append(
            f"{unread} citation(s) name this source with a reach that could not "
            f"be compared to it, so whether those positions are accounted for is "
            f"not a question this module answered")
    if gaps:
        states.append("GAPPED")
        why.append(
            "cards cite " + ", ".join(f"{low}–{high}" for low, high in gaps)
            + " and the reaches around them do not: the cited reaches skip them")
    if spans:
        states.append("COMPLETE")
    if no_reach:
        states.append("NO_REACH")
        why.append(
            f"{no_reach} citation(s) name this source without naming a reach, so "
            f"they account for no position in it. A citation that says it read a "
            f"source and not how far is not evidence of being current")
    state = _worst(states or ["NONE"], UNDERSTOOD_STATES)
    return no_unsourced({
        "source": source,
        "state": state,
        "runs": runs,
        "gaps": gaps,
        "spans": sorted([low, high] for low, high in spans),
        "why": why,
    }, UNDERSTOOD_PRODUCED, "understood")


# --------------------------------------------------------------------------
# the comparison
# --------------------------------------------------------------------------

def compare(capture: dict, understood: dict) -> dict:
    """The two waterlines against each other, with nothing merged away.

    The order the facts are assembled in is the order of the consequences: a
    hole in the capture is read before a lag, because a lag measured across a
    hole is a statement about positions that are not there.
    """
    if capture["kind"] != "POSITIONAL":
        return _uncompared(capture, understood)

    if capture["state"] == "UNREADABLE":
        # There is no extent to compare against, and `above(understood.runs,
        # None)` would report EVERY reach as an overreach. That would be this
        # module's own failure to read, rendered as a claim about the cards —
        # the comparison reporting the opposite of what it measured. Both
        # differences stay empty, and the state says why.
        lag: list[list[int]] = []
        beyond: list[list[int]] = []
    else:
        lag = below(capture["runs"], understood["runs"])
        beyond = above(understood["runs"], capture["high"])

    why: list[str] = []
    if capture["gaps"]:
        why.append(
            "the capture holds no position at "
            + ", ".join(f"{low}–{high}" for low, high in capture["gaps"])
            + (" (which the records say is missing material)"
               if capture["state"] == "GAPPED" else
               " (and nothing on record says material is missing there)")
            + ": there is nothing to understand at those positions, and the lag "
              "below is not about them")
    if beyond:
        why.append(
            "understanding cites " + ", ".join(f"{low}–{high}" for low, high in beyond)
            + " past the capture's extent "
            + (f"({capture['high']})" if capture["high"] is not None else "(none)")
            + ": the compared side reaches further than the side it is compared "
              "against, which is not the same fact as being behind")
    if lag:
        why.append(
            "captured and not accounted for: "
            + ", ".join(f"{low}–{high}" for low, high in lag))
    if capture["tail"] == "OPEN":
        why.append(
            f"the capture's tail is open at {capture['high']}: everything past "
            f"it is unknown, so `lag` here is a lower bound on what is "
            f"unaccounted for and not a total")

    if capture["state"] == "UNREADABLE":
        state = "UNREADABLE"
    elif beyond:
        state = "INCONSISTENT"
    elif capture["state"] in ("GAPPED", "DUPLICATED", "LABELS_NOT_CONTIGUOUS"):
        # The capture's own word, reused rather than restated: a defect in the
        # capture has to be resolved before the comparison between the two
        # waterlines means anything, so the headline is that defect and not a
        # lag computed across it. Both readings of a hole are kept apart here
        # too, because they are already kept apart on the capture side.
        state = capture["state"]
    elif lag:
        state = "BEHIND"
    elif capture["tail"] == "OPEN":
        state = "TAIL_UNKNOWN"
    else:
        state = "UP_TO_DATE"
    return no_unsourced({
        "source": capture["source"],
        "state": state,
        "capture": capture,
        "understood": understood,
        "lag": lag,
        "unread_capture_gaps": capture["gaps"],
        "beyond_source": beyond,
        "why": why,
    }, COMPARISON_PRODUCED, "comparison")


def _uncompared(capture: dict, understood: dict) -> dict:
    """A source the positional comparison does not apply to.

    Two cases, and they are not the same answer. A DOCUMENT has no positions and
    no pagination, so `UP_TO_DATE` would be a claim this module has no way to
    support: a document whose digest no longer matches would read as current,
    because nothing positional exists to disagree with it. The answer says what
    that source's currency actually is and names the reader that decides it. An
    UNREADABLE capture object is a different matter — there a comparison was
    wanted and could not be made, and `lag: []` must not be read as "nothing is
    behind".
    """
    if capture["kind"] == "UNREADABLE":
        return no_unsourced({
            "source": capture["source"],
            "state": "UNREADABLE",
            "capture": capture,
            "understood": understood,
            "lag": [],
            "unread_capture_gaps": [],
            "beyond_source": [],
            "why": [
                "no comparison was made for this source: "
                + ("; ".join(capture["why"]) or "the capture object states no reason")
                + ". The empty `lag` here is the absence of a comparison and not "
                  "the absence of a lag",
            ],
        }, COMPARISON_PRODUCED, "comparison")
    why = [
        f"`{capture['source']}` has no positions to be behind on: this module "
        f"compares reaches, and there is no reach here. Its currency is decided "
        f"by `understanding_delta`'s DIGEST measurement, not by anything in this "
        f"row"
    ]
    if capture["state"] != "COMPLETE":
        why.insert(0, "the capture object for this source is not COMPLETE: "
                   + ("; ".join(capture["why"]) or "no reason recorded"))
    return no_unsourced({
        "source": capture["source"],
        "state": "NOT_POSITIONAL",
        "capture": capture,
        "understood": understood,
        "lag": [],
        "unread_capture_gaps": [],
        "beyond_source": [],
        "why": why,
    }, COMPARISON_PRODUCED, "comparison")


def unresolved_row(source: str, why: str) -> dict:
    """A source a card cites that no capture object holds.

    Without this row the waterlines document would list only what the capture
    tree holds, and a card citing a source that was never captured — or that was
    captured and is now gone — would appear nowhere in it. A source missing from
    a coverage report reads as a source with nothing wrong.
    """
    understood = no_unsourced({
        "source": source, "state": "NONE", "runs": [], "gaps": [], "spans": [],
        "why": [],
    }, UNDERSTOOD_PRODUCED, "understood")
    return no_unsourced({
        "source": source,
        "state": "UNRESOLVED",
        "capture": None,
        "understood": understood,
        "lag": [],
        "unread_capture_gaps": [],
        "beyond_source": [],
        "why": [why],
    }, COMPARISON_PRODUCED, "comparison")


def waterlines(records_root: Path | str, delta, objects: list[dict]) -> dict:
    """Every source in the capture tree, plus every source a card cites.

    `delta` is the `understanding_delta` module and `objects` is
    `capture_coverage`'s output. Both are arguments for the same reason the
    modules above take their inputs as arguments: one reader per tree, and this
    is the composition and not a third reader.
    """
    root = Path(records_root)
    report = delta.read_and_reconcile(root, objects)
    rows_by_source: dict[str, list[dict]] = {}
    unresolved: list[dict] = []
    for card in report["cards"]:
        for row in card.get("measurements", []):
            manifest = row.get("manifest")
            if manifest:
                rows_by_source.setdefault(manifest, []).append(row)
            elif row.get("kind") == "UNRESOLVED":
                unresolved.append(row)

    out = []
    for obj in objects:
        key = obj.get("path")
        if not key:
            continue
        shape = obj.get("shape")
        rows = rows_by_source.get(key, [])
        kind = ("POSITIONAL" if shape == "chat_capture" else
                "DOCUMENT" if shape == "document_capture" else "UNREADABLE")
        capture = capture_waterline(
            obj,
            (lambda key=key: delta.read_position_runs(
                delta.messages_file(key, root))))
        understood = understood_waterline(key, kind, rows)
        out.append(compare(capture, understood))

    # Grouped by the cited path, because that is what a person can look for. Two
    # cards citing one absent source is one row with two reasons; two cards
    # citing two absent sources is two rows.
    for row in unresolved:
        source = row.get("source")
        if source is None:
            raise Refused(
                "an UNRESOLVED measurement row carries no `source`, so the "
                "citation it came from cannot be named and this document would "
                "report a source it cannot point at")
        out.append(unresolved_row(source, row.get("unresolved_why") or ""))
    return no_unsourced({
        "schema": SCHEMA,
        "root": str(root),
        "sources": sorted(out, key=lambda row: (str(row["source"]), row["state"])),
    }, WATERLINES_PRODUCED, "waterlines")


def render(waterlines_document: dict) -> list[str]:
    """The waterlines as lines, holes before lag, because that is the order."""
    lines = [f"root        {waterlines_document['root']}"]
    rows = waterlines_document["sources"]
    if not rows:
        lines.append("sources     0: the capture tree holds no manifest and no "
                     "card cites one")
        return lines
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    lines.append("sources     " + " · ".join(
        f"{name} {counts[name]}" for name in COMPARISON_STATES if name in counts))
    lines.append("")
    for row in rows:
        capture = row["capture"]
        understood = row["understood"]
        lines.append(f"{row['source']}  {row['state']}")
        if capture is None:
            lines.append("    capture    —（没有捕获对象持有这个来源；"
                         "上面这行是卡片写下的路径，不是捕获侧的键）")
        else:
            lines.append(f"    capture    {_runs_text(capture['runs'])}"
                         f"  高 {capture['high']}  尾 {capture['tail']}"
                         f"  {capture['kind']} {capture['state']}")
            if capture["gaps"]:
                lines.append(f"    capture 缺 {_runs_text(capture['gaps'])}")
            if capture["duplicates"]:
                lines.append("    capture 重 " + _runs_text(
                    [[p, p] for p in capture["duplicates"]]))
        lines.append(f"    understood {_runs_text(understood['runs'])}"
                     f"  {understood['state']}")
        if row["lag"]:
            lines.append(f"    lag        {_runs_text(row['lag'])}")
        if row["beyond_source"]:
            lines.append(f"    越过       {_runs_text(row['beyond_source'])}")
        for sentence in row["why"]:
            lines.append(f"    · {sentence}")
        # Both sides' own reasons, labelled with the side that wrote them. The
        # comparison's sentence says what the pair amounts to; what a hole MEANS
        # is the capture side's sentence and it is not a restatement of the
        # comparison's, so a render that printed only one of the two would drop
        # the reading and keep the label.
        for label, part in (("capture", capture), ("understood", understood)):
            for sentence in (part or {}).get("why") or []:
                lines.append(f"    {label}：{sentence}")
    return lines
