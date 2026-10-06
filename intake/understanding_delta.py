#!/usr/bin/env python3
"""What each understanding card claims to have read, against what the sources now hold.

WHY THIS FILE EXISTS
--------------------
`records/information/understanding/README.md` states one rule as a requirement
rather than a preference: 每份认识必须能溯源到 raw source. `intake/workchain.py`
counts documents that carry a `## Sources` heading, which measures whether the
rule was *written down*. Nothing measured whether it was *true*, and the
difference between those two is the whole of this module.

The layer is hand-authored. Every card's `Sources` region cites raw paths, and
most of them cite `message_position` ranges as well — so a card already carries,
in its own text, the newest source position it demonstrably looked at. This
module reads that, reads what the sources now hold, and reports the difference.
It is the reconciliation half of the design's 最小 incremental writer: a writer
needs to know which cards a source delta touches, and that question is
answerable without reading a single message body.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not read message content, and therefore **emits no claim**. A proposed
claim is a semantic statement about what a message means, and a reader that
produced one from a position range would be inventing it. What it emits is a
range to look at (`review_range`) and the card it belongs to. Nothing downstream
can mistake a range for a claim, which is why the field is not named `claim`.

It does not write. The cards are the operator's own record; this reports and the
person edits.

It does not grade a card whose marker it found but could not follow. That card is
`UNREADABLE`, with the reason naming what was missing.

It holds no clock. There is no `import time` and no `import datetime`; every
timestamp it reports is one it read out of a manifest, attributed to that
manifest.

TWO WATERMARKS, NEVER ONE
-------------------------
The design requires that a source which reads fine and an understanding that
lags it are two different lags, both visible. So a card is never given a single
"how current is this" number. It gets:

  * `applied` — the newest position the CARD's own citations reach
  * `source`  — the newest position the SOURCE now holds, plus the manifest's
                own independent count

and the lag is the difference between two positions, computed only when both are
positions. A count is not a position: comparing `msg_count` to a cited
`message_position` would be reading two different things off two different
rulers, and the arithmetic would look fine.

`CURRENT` is not the only way a card can be right. A card can be at the newest
position and still be wrong, because a message it already read was **edited or
deleted in place** — same position, different content. That is `STALE_IN_PLACE`,
and it is a separate state because the fix is different: not "read further" but
"re-read what you already read".

A POSITION IS NOT A VERSION
---------------------------
`STALE_IN_PLACE` names the fact that a position can hold a different text than
the one a card read there. Detecting it once is not the same as being able to
say WHICH text a proposal is about, and a proposal that cannot say that cannot
carry a confirmation: a person who approved "re-read 700–720" would still be
recorded as having approved it after those messages were rewritten, which is an
approval of a revision nobody read.

So every range this module proposes carries `content_identity` — a digest over
the records the range names, or the digest the manifest recorded for a document.
Two proposals naming the same card, capture and span are the same proposal only
when this agrees as well. When it does not, the range is the same PLACE holding
different CONTENT, and an older decision about it is about something that is no
longer there. The write half keys its decisions on this, which is why a
confirmation can stop applying without anybody editing the card.

THE DIGEST IS ONE-WAY, AND THE NO-CLAIM RULE STANDS
---------------------------------------------------
Hashing a record means reading it, and this module states above that it does not
read message content. What it holds is a rule about what it REPORTS: no body, no
excerpt and no paraphrase leaves this module, and a sha256 cannot carry one. A
digest answers "is this the same text" and never "what does it say". The
producer guard is what keeps that honest — no field named `content` can be
emitted here without an entry in the table saying where its value came from, and
there is no such entry.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

SCHEMA = "serveros/understanding-delta.v1"

#: Where the cards live, and where the sources they cite live. Both under the
#: records root, so one root is enough for a caller.
UNDERSTANDING_SEGMENTS = ("information", "understanding")
#: The same prefix `capture_coverage.raw_root` walks, and therefore the prefix
#: its objects' `path` fields are relative to. Kept as a literal pair rather
#: than imported from that module because this is the COORDINATE SYSTEM a
#: citation is written in, and reading it from the other module would make a
#: reader's key space depend on the writer's internals.
RAW_SEGMENTS = ("information", "raw")
#: Where the collectors write the chats, below the raw root.
CAPTURE_SEGMENTS = ("feishu", "chats")

#: The marker the layer's README names. Accepted in two shapes because the
#: node's own cards use both: `## Sources` (the project and topic cards) and a
#: bold bullet `- **Sources**：` (the people cards). `workchain.py` counts only
#: the heading form, and that is not a defect to fix there — it is a real
#: difference between the scopes, and this module reports which form each card
#: used so the two counts can be reconciled instead of contradicting.
SOURCES_HEADING = "Sources"

#: `UNREADABLE` is a card whose own text this reader could not follow (a
#: dangling position, a source claim with no locator). It is a fact about the
#: card and it is reported per card, never as a refusal.
CARD_STATES = ("CURRENT", "BEHIND", "STALE_IN_PLACE", "UNTRACEABLE",
               "UNMEASURABLE", "UNREADABLE")

#: How one citation was measured. `POSITION` compares two positions;
#: `DIGEST` compares a cited digest prefix to the digest the manifest recorded;
#: `WHOLE_FILE` means the card cited a source without saying how far into it it
#: read, so there is no reach to be behind; `UNRESOLVED` means the cited path
#: matched no source this node captured.
MEASUREMENTS = ("POSITION", "DIGEST", "WHOLE_FILE", "UNRESOLVED")

#: Emitted under `measurement`, each key beside where its value comes from. The
#: guard in `no_unsourced` refuses any key not named here, so a future field has
#: to state its producer before it can leave this module.
MEASUREMENT_PRODUCED = {
    "kind": "which of MEASUREMENTS this row is, decided by what the CITATION "
            "carried and by whether the source's own positions are readable",
    "source": "the cited path, echoed from the card",
    "via_glob": "the source key this row came from, when the citation named a set",
    "manifest": "the capture object's path relative to the capture root",
    "shape": "the capture object's own shape (chat_capture/document_capture)",
    "applied": "the newest `message_position` in the citation's own ranges",
    "applied_low": "the oldest `message_position` in the citation's own ranges",
    "source_positions": "state/count/min/max/edited/deleted read from the "
                        "source's messages.jsonl, beside why the max is not "
                        "decidable when it is not",
    "source_count": "`sync.msg_count` — the collector's count, independent of "
                    "the positions, from the manifest",
    "independent_count": "`sync.remote_unique_message_count` when the manifest "
                         "carries one, i.e. a count the collector did not author",
    "latest_create_time": "`sync.latest_create_time` from the manifest",
    "last_message_id": "`sync.last_message_id` from the manifest",
    "lag": "source max - applied, present only when both are positions",
    "revisions_in_range": "edited/deleted counts among the messages at or below "
                          "`applied` — the ones the card already read",
    "cited_digest": "the digest prefix the card quoted",
    "content_sha256": "the digest the manifest recorded for that content",
    "digest_why": "why a cited digest could not be compared",
    "coverage": "the source component states that are not COMPLETE, copied from "
                "capture_coverage so a lag and a capture gap stay apart",
    "unresolved_why": "why a cited path matched nothing, or why a cited reach "
                      "could not be compared to the source",
}

#: Emitted under `review_range` — what a person (or a semantic layer) should
#: look at. Named for what it is. See the module docstring.
REVIEW_PRODUCED = {
    "card": "the understanding card, relative to the records root",
    "source": "the cited source path",
    "kind": "why there is a range: BEYOND_APPLIED, REVISITED_IN_PLACE, or "
            "WHOLE_DOCUMENT",
    "from_position": "the first position to re-read, or null for a range that "
                     "is not positional",
    "to_position": "the last position to re-read, or null for a range that is "
                   "not positional",
    "latest_create_time": "`sync.latest_create_time`, from the manifest",
    "last_message_id": "`sync.last_message_id`, from the manifest",
    "revisions": "edited/deleted counts inside the range, for REVISITED_IN_PLACE",
    "cited_digest": "the digest prefix the card quoted, for WHOLE_DOCUMENT",
    "content_sha256": "the digest the manifest now records, for WHOLE_DOCUMENT",
    # The version pair, copied from the measurement this range came out of.
    # Copied rather than recomputed: a range that said only "positions 761-1491"
    # would be a range a reader cannot check, because the other half of the
    # comparison — where the card had got to, and where the source got to — is
    # what the proposal rests on. `manifest` is here for the glob case: one
    # cited path can match several capture objects, and a range that does not
    # say which one is not actionable.
    "manifest": "the capture object this range was measured against, from the "
                "capture index",
    "applied": "the newest position the card cites in that source",
    "applied_low": "the oldest position the card cites in that source",
    "source_max": "the newest position that source now holds, from its own "
                  "messages file",
    "source_count": "`sync.msg_count` — the collector's count, from the manifest",
    # The version half. A range says WHERE to read; this says WHAT was there
    # when the proposal was computed, which is the other half of a thing a
    # person can be asked to confirm. See the module docstring.
    "content_identity": "a digest of the records this range names, computed from "
                        "the source's own messages file; for a WHOLE_DOCUMENT "
                        "range, the digest the manifest recorded. Null only when "
                        "the records could not be read at the moment the range "
                        "was computed, which `content_identity_why` then names",
    "content_identity_why": "why `content_identity` is null, present only when "
                            "it is",
}

#: Emitted under `judged` — the card's state and why, plus how much of the card
#: the state actually covers. `partial` exists because a verdict over the
#: citations that were compared is not a verdict over the card.
VERDICT_PRODUCED = {
    "state": "one of CARD_STATES, from the precedence stated in judge_card",
    "why": "which measurements produced that state, in words",
    "review_ranges": "the ranges the state implies, from REVIEW_PRODUCED",
    "measured": "citations that named a reach and were compared",
    "unmeasured": "citations that named no reach, so nothing was compared",
    "unresolved": "citations that resolved to no captured source, so they were "
                  "not read at all",
    "partial": "true when the state covers some of the card's citations and not "
               "all of them, whether because a citation named no reach or "
               "because it resolved to nothing",
}

_LOCATOR = re.compile(r"`([^`]*information/raw/[^`]*)`")
_POSITION_CLAUSE = re.compile(
    r"message_position\s*([0-9]+(?:\s*-\s*[0-9]+)?"
    r"(?:\s*[、,，]\s*[0-9]+(?:\s*-\s*[0-9]+)?)*)")
_SHA = re.compile(r"\bsha\s*`?([0-9a-f]{6,64})`?")
_HEADING_MARKER = re.compile(r"^#{1,6}\s*" + SOURCES_HEADING + r"\s*[:：]?\s*$")
_BULLET_MARKER = re.compile(r"^\s*[-*]\s*\*\*" + SOURCES_HEADING + r"\*\*\s*[:：]?")
_ANY_HEADING = re.compile(r"^#{1,6}\s")
#: A line that ends the region rather than continuing it: flush left and not a
#: list item, a block quote or an ordered-list marker. Indented continuations,
#: bullets and quotes belong to the Sources region; the first ordinary sentence
#: after it does not.
_STOPS_REGION = re.compile(r"^(?![ \t])(?![-*+>])(?!\d+[.)])\S")


class Refused(RuntimeError):
    """This reader will not answer.

    Raised for a defect in the READER — an emitted key no producer table names,
    an absent root. It is deliberately not the exception for a card this reader
    cannot follow: swallowing the two together would report a card as unreadable
    because the reader had a bug, which blames the author for the tool.
    """


class Malformed(Refused):
    """One card is not traceable as written. A fact about the CARD, not the reader."""


def understanding_root(records_root: Path) -> Path:
    return Path(records_root).joinpath(*UNDERSTANDING_SEGMENTS)


def raw_root(records_root: Path) -> Path:
    """The tree `capture_coverage` keys its objects against.

    NOT the chats directory below it. The keys in the index are relative to this
    root, so joining a key onto a deeper root doubles the prefix and lands on a
    path that does not exist — which reads as "this source has no messages file"
    rather than as a bug, and turns every card into `UNMEASURABLE` while looking
    like a fact about the cards.
    """
    return Path(records_root).joinpath(*RAW_SEGMENTS)


def walk_cards(root: Path) -> list[Path]:
    """Every card under the understanding root, sorted, as paths.

    `README.md` is excluded at any depth: it states the layer's rules and is not
    itself an understanding of anything. Counting it would add to the
    denominator a document whose `Sources` region describes the rule.
    """
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            if name.endswith(".md") and name != "README.md":
                found.append(Path(dirpath) / name)
    return sorted(found)


def find_sources_region(text: str) -> tuple[str, int, list[str]]:
    """`(marker_form, line_index, lines)` for a card's Sources region.

    The LAST marker wins. A card that mentions sources in its opening prose and
    then has a real region must be read at the region, and taking the first
    match would read a sentence about the rules as the list of sources.
    """
    lines = text.splitlines()
    marker = -1
    form = "absent"
    for index, line in enumerate(lines):
        if _HEADING_MARKER.match(line):
            marker, form = index, "heading"
        elif _BULLET_MARKER.match(line):
            marker, form = index, "bold-list"
    if marker < 0:
        return "absent", -1, []

    region = [lines[marker]]
    for line in lines[marker + 1:]:
        if _ANY_HEADING.match(line) or _STOPS_REGION.match(line):
            break
        region.append(line)
    return form, marker, region


def parse_citations(region: list[str]) -> list[dict]:
    """The sources one Sources region cites, with what was said about each.

    THE ATTACHMENT RULE, which is a decision and not an accident: a
    `message_position` clause or a `sha` clause attaches to the **nearest
    preceding** backticked raw path. The node's own cards write it that way —
    two files and two position clauses inside one bullet — and any other rule
    would have to guess which mention went with which file.

    A clause that precedes every path in its region attaches to nothing. That is
    `Malformed`: the card says it read up to here and does not say up to here in
    what, and dropping the clause quietly would report the card as having cited
    a source in full when it cited a position in something unnamed.
    """
    citations: list[dict] = []
    current: dict | None = None
    for line in region:
        if not line.strip():
            continue
        clauses = sorted(
            [("path", m) for m in _LOCATOR.finditer(line)]
            + [("positions", m) for m in _POSITION_CLAUSE.finditer(line)]
            + [("digest", m) for m in _SHA.finditer(line)],
            key=lambda pair: pair[1].start())
        for kind, match in clauses:
            if kind == "path":
                raw = match.group(1).strip().rstrip("；;，,).")
                current = {"path": raw, "ranges": [], "digest": None,
                           "glob": any(ch in raw for ch in "*?[")}
                citations.append(current)
            elif kind == "positions":
                if current is None:
                    raise Malformed(
                        f"a `message_position` clause names positions in "
                        f"nothing: {line.strip()[:120]!r} places it before any "
                        f"backticked raw path in the region")
                current["ranges"].extend(_ranges(match.group(1)))
            else:
                if current is None:
                    raise Malformed(
                        f"a `sha` clause names a digest of nothing: "
                        f"{line.strip()[:120]!r} places it before any backticked "
                        f"raw path in the region")
                current["digest"] = match.group(1).lower()
    return citations


def _ranges(clause: str) -> list[tuple[int, int]]:
    out = []
    for part in re.split(r"[、,，]", clause):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            low, high = (piece.strip() for piece in part.split("-", 1))
            out.append((int(low), int(high)))
        else:
            out.append((int(part), int(part)))
    return out


def cited_span(citation: dict) -> tuple[int, int] | None:
    """`(oldest, newest)` position this citation reaches, or `None` if it names none."""
    if not citation["ranges"]:
        return None
    return (min(low for low, _ in citation["ranges"]),
            max(high for _, high in citation["ranges"]))


def read_card(path: Path, records_root: Path) -> dict:
    """One card as a traceability record. Always a record, never an exception."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"state": "UNREADABLE", "why": f"cannot read {path}: {exc}"}
    title = ""
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    form, _, region = find_sources_region(text)
    try:
        citations = parse_citations(region)
    except Malformed as exc:
        return {"state": "UNREADABLE", "sources_marker": form, "title": title,
                "why": str(exc), "card": str(path.relative_to(records_root))}
    parts = path.relative_to(understanding_root(records_root)).parts
    spans = [span for span in (cited_span(c) for c in citations) if span]
    reach = max((high for _, high in spans), default=None)
    if form == "absent":
        state = "UNTRACEABLE"
        why = (f"no `{SOURCES_HEADING}` region in any accepted form (a heading, "
               f"or a `- **{SOURCES_HEADING}**：` bullet), so nothing here can be "
               f"checked against a source. The layer's own README makes a source "
               f"reference a requirement, so this is reported as a card with "
               f"nothing to measure and not as one that is up to date")
    elif not citations:
        state = "UNTRACEABLE"
        why = ("the region is present and cites no raw path, so the card claims "
               "traceability without naming anything to check")
    else:
        state, why = None, ""
    return {
        "card": str(path.relative_to(records_root)),
        # The understanding baseline, as an identity rather than a path: a card
        # rewritten in place keeps its path and its citations can keep their
        # spans, and a proposal computed against the newer text is not the
        # proposal a person read. Nothing here interprets the card — this is a
        # digest of the text this reader already had to load.
        "card_digest": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "scope": parts[0] if len(parts) > 1 else None,
        "title": title,
        "sources_marker": form,
        "citations": citations,
        "applied_low": min((low for low, _ in spans), default=None),
        "cited_reach": reach,
        "state": state,
        "why": why,
        "judged": {},
    }


def read_root(records_root: Path) -> dict:
    """Every card under `<records_root>/information/understanding`."""
    root = understanding_root(records_root)
    if not root.is_dir():
        raise Refused(
            f"{root} is not a directory: there is no understanding layer to "
            f"read. An absent root is not an empty one, and reporting zero cards "
            f"here would say this node understands nothing when nothing was "
            f"looked at")
    cards = [read_card(path, records_root) for path in walk_cards(root)]
    return {"schema": SCHEMA, "root": str(root), "cards": cards,
            "summary": summarize(cards)}


def source_index(objects: list[dict]) -> dict[str, dict]:
    """`{capture-relative manifest path: capture object}`.

    Keyed by the object's OWN `path`, so a citation resolves through the same
    field every other reader of `capture_coverage` already trusts rather than
    through a path rebuilt here.
    """
    return {obj["path"]: obj for obj in objects if obj.get("path")}


def manifest_of(citation: dict) -> str | None:
    """The capture object a citation's path belongs to, or `None` if it is outside.

    TWO COORDINATE SYSTEMS, and a citation is written in the other one from the
    index. A card cites a path relative to the RECORDS root
    (`information/raw/feishu/chats/…`) because that is what a reader of the card
    can open; `capture_coverage` keys its objects relative to
    `RAW_SEGMENTS` (`feishu/chats/…`), because that is the tree it walks. The
    prefix is removed here rather than the index being rebuilt in the card's
    coordinates, because rebuilding it would give this module a second opinion
    about where the capture tree starts.

    The manifest is the SIBLING of what was captured, not the file itself:
    `.../oc_x/messages.jsonl` is described by `.../oc_x/manifest.json`, and a
    link's `content.md` by the manifest in its own directory, because that is
    where the collectors write it. So the answer is the citation's directory,
    which is a documented layout and not a guess about the file.
    """
    parts = citation["path"].split("/")
    prefix = list(RAW_SEGMENTS)
    if parts[:len(prefix)] != prefix:
        return None
    tail = parts[len(prefix):]
    if not tail:
        return None
    if tail[-1] == "manifest.json":
        return "/".join(tail)
    return "/".join(tail[:-1] + ["manifest.json"])


def _path_glob(pattern: str) -> re.Pattern:
    """A glob that does NOT cross a `/`.

    `fnmatch` lets `*` match separators, so `people/*/messages.jsonl` — a card
    naming each person's chat — would also match
    `people/<someone>/<chat>/links/<doc>/manifest.json`, i.e. every linked
    document under every person. The expansion would then be several times
    larger than what was cited, and every extra row would read as a source the
    card had cited when it had not. A citation glob is a path, so it is matched
    as one.
    """
    out = []
    for char in pattern:
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
    return re.compile("".join(out) + r"\Z")


def expand(citation: dict, index: dict) -> list[str]:
    """The index keys a citation names, each one checked against the index.

    A glob names a set; a path names itself — but only if the index holds it.
    Returning the composed key unchecked made a plain path that nothing matched
    a `KeyError` at the caller, which is the wrong answer twice over: it stops
    the walk at the first unresolvable citation, and it reports a citation that
    resolves to nothing as a crash rather than as the `UNRESOLVED` measurement
    the unresolved branch already knows how to write. The composed key is still
    what the caller needs for the message, so an unmatched path comes back as no
    keys and `measure_citation` names the key it looked for.
    """
    key = manifest_of(citation)
    if key is None:
        return []
    if not citation["glob"]:
        return [key] if key in index else []
    matcher = _path_glob(key)
    return sorted(candidate for candidate in index if matcher.match(candidate))


def messages_file(key: str, records_root: Path) -> Path:
    """The messages file beside a capture object named by its manifest key.

    One derivation, in one place, because two of them drift and the one that
    drifts is the one that decides which file a digest was taken over.
    """
    directory = key.rsplit("/", 1)[0] if "/" in key else ""
    return raw_root(records_root).joinpath(
        *[part for part in directory.split("/") if part]) / "messages.jsonl"


def _scan_positions(source_file: Path) -> dict:
    """One messages file, parsed once. Both views below are projections of this.

    One parse and two projections rather than two readers of one file: two
    readers drift, and the one that drifts is the one that decides whether a
    position was there.

    Only `message_position` and the revision flags are read into any returned
    value. No `content` is loaded, so nothing here can carry a message body into
    a report.
    """
    if not source_file.is_file():
        return {"state": "NO_MESSAGES_FILE", "count": 0, "positions": [],
                "duplicates": [], "edited": 0, "deleted": 0, "without_position": 0,
                "why": f"{source_file} does not exist"}
    count = edited = deleted = without = 0
    positions: list[int] = []
    try:
        handle = source_file.open(encoding="utf-8")
    except OSError as exc:
        return {"state": "UNREADABLE", "count": 0, "positions": [],
                "duplicates": [], "edited": 0, "deleted": 0, "without_position": 0,
                "why": str(exc)}
    with handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError as exc:
                return {"state": "MALFORMED", "count": count, "positions": [],
                        "duplicates": [], "edited": edited, "deleted": deleted,
                        "without_position": without,
                        "why": f"line {number}: {exc}"}
            if not isinstance(record, dict):
                return {"state": "MALFORMED", "count": count, "positions": [],
                        "duplicates": [], "edited": edited, "deleted": deleted,
                        "without_position": without,
                        "why": f"line {number} is not an object"}
            count += 1
            if record.get("updated") or record.get("edited"):
                edited += 1
            if record.get("deleted"):
                deleted += 1
            try:
                positions.append(int(str(record.get("message_position")).strip()))
            except (TypeError, ValueError):
                without += 1
    ordered = sorted(positions)
    duplicates = sorted({position for position in ordered
                         if ordered.count(position) > 1})
    return {
        "state": "DECIDABLE" if not without else "POSITIONS_INCOMPLETE",
        "count": count,
        "positions": ordered,
        "duplicates": duplicates,
        "edited": edited, "deleted": deleted, "without_position": without,
        "why": "" if not without else
               f"{without} of {count} records carry no integer "
               f"`message_position`, so the newest position this source reaches "
               f"cannot be stated",
    }


def read_positions(source_file: Path) -> dict:
    """`{state, count, min, max, edited, deleted, without_position}` for one jsonl.

    A record with no readable `message_position` makes `max` **not decidable**
    rather than smaller: `max` over the records that happen to have one is a
    number about the reader's luck, and returning it would understate how far
    the source reaches by exactly the amount this reader failed to look at.
    """
    scan = _scan_positions(source_file)
    positions = scan["positions"]
    return {"state": scan["state"], "count": scan["count"],
            "min": min(positions) if positions else None,
            "max": max(positions) if positions else None,
            "edited": scan["edited"], "deleted": scan["deleted"],
            "without_position": scan["without_position"], "why": scan["why"]}


def read_position_runs(source_file: Path) -> dict:
    """The positions one file holds, as runs AND the gaps between them.

    `read_positions` answers "how far does this source reach" with a maximum, and
    it is right about that question. It is the wrong answer to "what has been
    captured": a file holding 0–4 and 9–13 reaches 13 and is missing four
    positions in the middle, and a reader given only the maximum reports the
    capture as complete up to 13. No scalar contains that gap, so this returns
    the runs and names the gaps — 任何中间 gap 都不能被最大位置掩盖.

    Interior gaps only. A run that starts above zero is where the captured
    conversation begins and not a hole, and the same at the end; only a position
    missing BETWEEN two captured ones is a fact about the capture.
    """
    scan = _scan_positions(source_file)
    positions = scan["positions"]
    runs: list[list[int]] = []
    for position in positions:
        if runs and position == runs[-1][1] + 1:
            runs[-1][1] = position
        elif runs and position <= runs[-1][1]:
            continue                      # a duplicate, reported separately
        else:
            runs.append([position, position])
    gaps = [[runs[index][1] + 1, runs[index + 1][0] - 1]
            for index in range(len(runs) - 1)
            if runs[index + 1][0] > runs[index][1] + 1]
    return {
        "state": scan["state"], "runs": runs, "gaps": gaps,
        "duplicates": scan["duplicates"], "count": scan["count"],
        "low": positions[0] if positions else None,
        "high": positions[-1] if positions else None,
        "why": scan["why"],
    }


def revisions_in_range(source_file: Path, low: int, high: int) -> dict:
    """Edited/deleted counts among the messages the CARD already read.

    These are the ones a `lag` of zero cannot see: a message at a position the
    card has already been through, whose content changed afterwards, leaves the
    card stale while every position comparison says it is current.
    """
    edited = deleted = 0
    try:
        handle = source_file.open(encoding="utf-8")
    except OSError:
        return {"edited": 0, "deleted": 0, "readable": False}
    with handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            try:
                position = int(str(record.get("message_position")).strip())
            except (TypeError, ValueError):
                continue
            if not low <= position <= high:
                continue
            if record.get("updated") or record.get("edited"):
                edited += 1
            if record.get("deleted"):
                deleted += 1
    return {"edited": edited, "deleted": deleted, "readable": True}


def range_content_digest(messages: Path, low: int | None,
                         high: int | None) -> tuple[str | None, str]:
    """`(digest, why)` for the records a span names — the version half of a range.

    WHY THIS DOES NOT READ LIKE THE REST OF THE MODULE
    --------------------------------------------------
    Everything above reads `message_position` and the two revision flags and
    nothing else, so that no code path can carry a message body into a report.
    This function reads whole records, because a digest over a projection is a
    claim about the projection and not about the content: hashing only the
    fields the flags already record would produce a `content_identity` that is
    blind to exactly the edits `STALE_IN_PLACE` exists to catch. What leaves is
    a sha256, which answers "is this the same text" and cannot answer "what does
    it say" — and `no_unsourced` keeps a `content` field from being emitted.

    The record is hashed in a canonical serialisation, so a collector that
    re-serialises the same records with the keys in another order has not
    changed anything. A collector that ADDS a field has: the record is not the
    same record, and every confirmation taken over it goes back to the person
    rather than being silently carried forward.

    `(None, why)` is the answer when the span cannot be identified — an
    unreadable file, a malformed line, a span holding no record. It is not an
    exception: a source that went missing between two reads is a fact about the
    source, and the range is emitted saying it could not be identified rather
    than being dropped or being given a digest of nothing.
    """
    if low is None or high is None:
        return None, ("this range names no positions, so there is no span to "
                      "take a digest over")
    if not messages.is_file():
        return None, "the messages file for this capture is not a file"
    digest = hashlib.sha256()
    identified = False
    try:
        handle = messages.open(encoding="utf-8")
    except OSError as exc:
        return None, f"the messages file could not be read: {exc}"
    with handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                return None, "a line of the messages file is not JSON"
            if not isinstance(record, dict):
                return None, "a line of the messages file is not an object"
            try:
                position = int(str(record.get("message_position")).strip())
            except (TypeError, ValueError):
                # A record with no position is outside every span, exactly as it
                # is outside every position comparison above.
                continue
            if not low <= position <= high:
                continue
            identified = True
            digest.update(json.dumps(record, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8"))
            digest.update(b"\n")
    if not identified:
        return None, (f"no record in the messages file carries a position in "
                      f"{low}–{high}, so the span names nothing to identify")
    return digest.hexdigest(), ""


def _components(obj: dict) -> dict:
    """The source's component states that are not COMPLETE, as copied facts."""
    out = {}
    for name, component in (obj.get("coverage") or {}).get("components", {}).items():
        if component.get("state") and component["state"] != "COMPLETE":
            out[name] = {"state": component["state"],
                         "why": (component.get("why") or [""])[0]}
    return out


def no_unsourced(mapping: dict, table: dict, where: str) -> dict:
    """Refuse to emit a key whose value the caller cannot name a source for.

    Applied where a record is EMITTED rather than wherever each key is
    produced, because a guard only at the producer protects the producer.
    """
    unexpected = sorted(set(mapping) - set(table))
    if unexpected:
        raise Refused(
            f"this reader would emit "
            f"{', '.join(f'`{where}.{key}`' for key in unexpected)}, which the "
            f"producer table does not name a source for; add the producer or "
            f"drop the field")
    return mapping


def _shared_facts(obj: dict) -> dict:
    """The manifest facts this module copies out, whatever the capture shape.

    Copied rather than derived: `source_count`, `latest_create_time` and
    `last_message_id` are the collector's own account, and this module's job is
    to compare that account to a citation, not to reconstruct it.
    """
    facts: dict = {}
    for component in ((obj.get("coverage") or {}).get("components") or {}).values():
        for name, value in (component.get("facts") or {}).items():
            facts.setdefault(name, value)
    return facts


def _measure_one(citation: dict, key: str, obj: dict, records_root: Path,
                 *, via_glob: str | None) -> dict:
    span = cited_span(citation)
    # `cited_span` answers `(oldest, newest)`; the names below are the ones the
    # record uses, and unpacking them in the quoting order is how a card that
    # reached position 116 gets reported as having reached 106.
    applied_low, applied = span if span else (None, None)
    digest = citation["digest"]
    shape = obj.get("shape")
    row: dict = {"source": citation["path"], "manifest": key, "shape": shape}
    if via_glob:
        row["via_glob"] = via_glob
    if applied is not None:
        row["applied"] = applied
        row["applied_low"] = applied_low

    facts = _shared_facts(obj)
    if facts.get("msg_count") is not None:
        row["source_count"] = facts["msg_count"]
    if facts.get("remote_unique_message_count") is not None:
        row["independent_count"] = facts["remote_unique_message_count"]
    if facts.get("latest_create_time") is not None:
        row["latest_create_time"] = facts["latest_create_time"]
    if facts.get("last_message_id") is not None:
        row["last_message_id"] = facts["last_message_id"]

    if shape in ("document_capture", "web_capture"):
        recorded = facts.get("content_sha256")
        row["kind"] = "DIGEST" if digest else "WHOLE_FILE"
        if digest:
            row["cited_digest"] = digest
            if recorded:
                row["content_sha256"] = recorded
                row["lag"] = 0 if recorded.startswith(digest) else 1
            else:
                row["digest_why"] = (
                    "the card quoted a digest and this manifest records no "
                    "`content_sha256`, so agreement cannot be shown either way")
        coverage = _components(obj)
        if coverage:
            row["coverage"] = coverage
        return no_unsourced(row, MEASUREMENT_PRODUCED, "measurement")

    # A chat capture. Positions come from the messages file, which sits beside
    # the manifest under the same capture-relative directory.
    messages = messages_file(key, records_root)
    positions = read_positions(messages)
    row["source_positions"] = {
        "state": positions["state"], "count": positions["count"],
        "min": positions["min"], "max": positions["max"],
        "edited": positions["edited"], "deleted": positions["deleted"],
        "why": positions["why"],
    }
    if applied is None:
        # The card cited this source without saying how far into it it read.
        # There is no reach to compare, so no lag is formed — and calling this
        # "current" would be the comparison nobody made.
        row["kind"] = "WHOLE_FILE"
    elif positions["state"] != "DECIDABLE":
        row["kind"] = "WHOLE_FILE"
        row["unresolved_why"] = (
            "the card cites a position in this source and the source's own "
            "positions are not fully readable, so how far the card got cannot "
            "be compared to how far the source reaches")
    else:
        row["kind"] = "POSITION"
        row["lag"] = max(0, positions["max"] - applied)
        row["revisions_in_range"] = revisions_in_range(
            messages, applied_low, applied)
    coverage = _components(obj)
    if coverage:
        row["coverage"] = coverage
    return no_unsourced(row, MEASUREMENT_PRODUCED, "measurement")


def measure_citation(citation: dict, index: dict, records_root: Path) -> list[dict]:
    """One citation measured against every source it names."""
    keys = expand(citation, index)
    if not keys:
        key = manifest_of(citation)
        if key is None:
            why = (f"`{citation['path']}` is outside the capture tree, whose root "
                   f"is `{'/'.join(RAW_SEGMENTS)}` under the records root, so it "
                   f"names a source this module does not read")
        elif citation["glob"]:
            # A glob that matches nothing is a different defect from a path that
            # matches nothing, and reporting both as "this node did not capture
            # it" would state a falsehood about the second: the node captured
            # plenty, the citation just does not say which. The `*` here does
            # not cross a `/`, so `people/*/messages.jsonl` — a card naming
            # "each person's chat" — matches nothing at the depth it is written,
            # exactly as a shell would match nothing.
            why = (f"`{citation['path']}` is a glob that matches no captured "
                   f"path at the depth it is written (`{key}`), because a `*` "
                   f"does not cross a `/` here or in a shell. The card names a "
                   f"set without naming its members, so which sources it read "
                   f"cannot be recovered from the citation; the sources "
                   f"themselves are on disk and were captured")
        else:
            why = (f"no capture object under the capture root matches `{key}`, "
                   f"so this path names a source this node did not capture")
        return [no_unsourced({
            "kind": "UNRESOLVED", "source": citation["path"],
            "unresolved_why": (why + ". A citation that resolves to nothing is an "
                               "unresolved reference and not an up-to-date one"),
        }, MEASUREMENT_PRODUCED, "measurement")]
    return [_measure_one(citation, key, index[key], records_root,
                         via_glob=key if citation["glob"] else None)
            for key in keys]


def _review_range(card: dict, row: dict, kind: str, low, high,
                  records_root: Path) -> dict:
    identity, identity_why = _content_identity(row, kind, low, high, records_root)
    record = no_unsourced({
        "card": card["card"], "source": row["source"], "kind": kind,
        "from_position": low, "to_position": high,
        "latest_create_time": row.get("latest_create_time"),
        "last_message_id": row.get("last_message_id"),
        "manifest": row.get("manifest"),
        "applied": row.get("applied"),
        "applied_low": row.get("applied_low"),
        "source_max": (row.get("source_positions") or {}).get("max"),
        "source_count": row.get("source_count"),
        "content_identity": identity,
    }, REVIEW_PRODUCED, "review_range")
    if identity_why:
        record["content_identity_why"] = identity_why
    if kind == "REVISITED_IN_PLACE":
        record["revisions"] = dict(row["revisions_in_range"])
    if kind == "WHOLE_DOCUMENT":
        # A document has no positions, so this range carries the two digests
        # instead of a span. Without them the reader would be saying "re-read
        # this" while the only evidence it has for saying so is the pair of
        # hashes it just compared — which is exactly what a reader of the
        # proposal needs in order to agree or disagree with it.
        record["cited_digest"] = row.get("cited_digest")
        record["content_sha256"] = row.get("content_sha256")
    return record


def _content_identity(row: dict, kind: str, low, high,
                      records_root: Path) -> tuple[str | None, str]:
    """The digest for one range, and why there is none when there is none.

    One function rather than a branch at each range: the identity is a property
    of the RANGE, and a reader that computed it in three places would be three
    readers of one rule, two of which are wrong.
    """
    if kind == "WHOLE_DOCUMENT":
        recorded = row.get("content_sha256")
        if recorded:
            return recorded, ""
        return None, ("the manifest records no digest for this document, so the "
                      "revision this range is about cannot be named")
    manifest = row.get("manifest")
    if not manifest:
        return None, ("the range names no capture object, so there is no "
                      "messages file to identify its content from")
    return range_content_digest(messages_file(manifest, records_root), low, high)


def judge_card(card: dict, measurements: list[dict], records_root: Path) -> dict:
    """The card's state, the ranges to look at, and the reason for both.

    PRECEDENCE, stated rather than emergent: `BEHIND` beats `STALE_IN_PLACE`
    beats `CURRENT`. A card that has not read far enough is behind whatever else
    is true of it, because reading further is what it has to do first. A card at
    the newest position whose already-read messages changed is `STALE_IN_PLACE`.
    A card whose every citation names a source without naming a reach is
    `UNMEASURABLE`, not `CURRENT` — nothing was compared.

    `records_root` is here because a range now carries the identity of the
    content it names, and that identity is read from the source tree. Deciding
    which ranges a card implies and saying what those ranges are ABOUT are the
    same act; splitting them would let a caller emit a range whose version half
    nobody computed.
    """
    if card.get("state"):
        return no_unsourced(
            {"state": card["state"], "why": card["why"], "review_ranges": [],
             "measured": 0, "unmeasured": 0, "partial": False},
            VERDICT_PRODUCED, "verdict")

    behind = [row for row in measurements
              if row.get("kind") == "POSITION" and (row.get("lag") or 0) > 0]
    stale = [row for row in measurements
             if (row.get("revisions_in_range") or {}).get("edited")
             or (row.get("revisions_in_range") or {}).get("deleted")]
    unresolved = [row for row in measurements if row.get("kind") == "UNRESOLVED"]
    unmeasurable = [row for row in measurements
                    if row.get("kind") == "WHOLE_FILE"]
    digest_differs = [row for row in measurements
                      if row.get("kind") == "DIGEST" and row.get("lag")]
    measured = [row for row in measurements
                if row.get("kind") in ("POSITION", "DIGEST")]

    ranges = [
        _review_range(card, row, "BEYOND_APPLIED", row["applied"] + 1,
                      row["source_positions"]["max"], records_root)
        for row in behind
    ] + [
        _review_range(card, row, "REVISITED_IN_PLACE", row["applied_low"],
                      row["applied"], records_root)
        for row in stale
    ] + [
        # A cited digest that no longer matches the manifest is a state that
        # implies work and carries no position: the document was re-published,
        # and which part of it changed is not recorded anywhere this reader can
        # see. It used to contribute the state and no range at all, so the
        # verdict said STALE_IN_PLACE and proposed nothing to look at. A range
        # with no positions says "the whole document" without inventing a span.
        _review_range(card, row, "WHOLE_DOCUMENT", None, None, records_root)
        for row in digest_differs
    ]

    if behind:
        state = "BEHIND"
        why = (f"{len(behind)} source(s) hold positions past the newest one this "
               f"card cites")
    elif stale:
        state = "STALE_IN_PLACE"
        why = ("this card reaches the newest position its sources hold, and "
               f"{len(stale)} of those sources have messages inside the range it "
               f"already read that were edited or deleted afterwards")
    elif digest_differs:
        state = "STALE_IN_PLACE"
        why = ("the cited content digest no longer matches the one the manifest "
               "records for that path")
    elif measured:
        # A card can be partly measured: some citations name a reach and some do
        # not. `CURRENT` is the verdict for the ones that were compared, and
        # `partial` says that is not the whole card — reporting the whole card as
        # current would launder the citations nobody could check into checked
        # ones, and reporting it UNMEASURABLE would throw away the comparison
        # that did happen.
        #
        # TWO KINDS OF UNCHECKED, and both make the card partial. A citation that
        # names no reach was not compared; a citation that resolves to nothing
        # was not even read, and counting only the first would report the second
        # as checked by leaving it out of `partial` — the same laundering by a
        # narrower door.
        state = "CURRENT"
        unchecked = []
        if unmeasurable:
            unchecked.append(f"{len(unmeasurable)} further citation(s) name no "
                             f"reach and were not compared")
        if unresolved:
            unchecked.append(f"{len(unresolved)} further citation(s) resolve to "
                             f"no captured source, so there was nothing to "
                             f"compare them against")
        why = ("every citation that names a reach is at the source's newest "
               "position" + ("; " + "; ".join(unchecked) if unchecked else ""))
    elif unresolved:
        state = "UNMEASURABLE"
        why = (f"{len(unresolved)} citation(s) resolve to no captured source, so "
               f"there is nothing to compare against")
    elif unmeasurable:
        state = "UNMEASURABLE"
        why = (f"all {len(unmeasurable)} citation(s) name a source without "
               f"naming how far into it the card read, so there is no reach to "
               f"be behind")
    else:
        state = "UNMEASURABLE"
        why = "the card cites sources and none of them could be measured"
    return no_unsourced({
        "state": state, "why": why, "review_ranges": ranges,
        "measured": len(measured), "unmeasured": len(unmeasurable),
        "unresolved": len(unresolved),
        "partial": bool(measured and (unmeasurable or unresolved)),
    }, VERDICT_PRODUCED, "verdict")


def summarize(cards: list[dict]) -> dict:
    """Counts per state, per scope and per marker form.

    Counted and not graded, and no layer-level verdict is formed: a rollup that
    says the understanding layer is 3/12 current would be this module inventing
    a rule about how much of a layer must be fresh.
    """
    by_state: dict[str, int] = {}
    by_scope: dict[str, dict] = {}
    markers: dict[str, int] = {}
    for card in cards:
        state = (card.get("judged") or {}).get("state") or card.get("state")
        by_state[state] = by_state.get(state, 0) + 1
        bucket = by_scope.setdefault(card.get("scope") or "(root)", {})
        bucket[state] = bucket.get(state, 0) + 1
        marker = card.get("sources_marker", "absent")
        markers[marker] = markers.get(marker, 0) + 1
    return {"denominator": len(cards), "by_state": by_state,
            "by_scope": by_scope, "by_sources_marker": markers}


def reconcile(cards: list[dict], index: dict, records_root: Path) -> dict:
    """Per card: what it cites, what the source now holds, and the difference."""
    judged = []
    for card in cards:
        measurements: list[dict] = []
        for citation in card.get("citations", []):
            measurements.extend(measure_citation(citation, index, records_root))
        judged.append({**card, "measurements": measurements,
                       "judged": judge_card(card, measurements, records_root)})
    return {"schema": SCHEMA, "records_root": str(records_root),
            "root": str(understanding_root(records_root)),
            "cards": judged, "summary": summarize(judged)}


def read_and_reconcile(records_root: Path, objects: list[dict]) -> dict:
    """The whole reading, from the records root and `capture_coverage`'s objects.

    `objects` is taken as an argument rather than recomputed here so this module
    holds no second reader of the capture tree: two readers of one tree drift,
    and the one that drifts is the one nobody feeds.
    """
    root = Path(records_root)
    read = read_root(root)
    return reconcile(read["cards"], source_index(objects), root)
