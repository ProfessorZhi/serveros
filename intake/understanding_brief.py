#!/usr/bin/env python3
"""Fixed-version material for a native Agent, and the two digests it hands back.

WHY A PACKET AND NOT "READ THE RECORDS"
--------------------------------------
`understanding_semantic` is a validator. It is handed a reading and decides
whether the version that reading was made against is the version the source is
at now (`_check_binding`), and it can do that because it never fetched anything
itself: a writer that loaded its own material would be a writer whose reading is
about whatever it happened to load. What was missing was the other end of that
— something that hands a native Agent the material at a FIXED version and says
which two digests the reading must carry back. That is this module, and it is
the only thing in the tree that assembles material for a reader.

THE PACKET IS THE PIN
---------------------
`source` and `read` are emitted here exactly as `understanding_semantic`
compares them, so pinning the version is not a discipline the Agent has to
remember: the two blocks it must write are in front of it, copied, and a
reading carrying anything else is refused by name at the commit. `material` is
what those two blocks name — the records in the span, in position order, as the
collector wrote them — so "the fixed version" and "the text" are the same
object rather than two things that are usually the same.

WHAT A MATERIAL ROW CARRIES, AND WHAT IT DOES NOT
-------------------------------------------------
A row carries `position`, `content`, `updated` and `deleted`: the record's text
and the two revision flags that change what the text means. It carries nothing
else, for the reason `understanding_delta._scan_positions` gives from the other
side — every other field of a captured record is about a person or about
capture mechanics, and this packet is read by a model. The identity the Agent
hands back is taken over the WHOLE record, so this is a projection of the thing
the digest covers; the argument for that asymmetry is that function's
docstring, not a second one here.

A `deleted` record is included with its text. A withdrawal is exactly what a
reading most needs to see, and a packet that dropped it would hand the Agent a
source that looked like it had never said the thing.

WHAT THIS MODULE DOES NOT DO
----------------------------
No model client, no clock, and no writes: a brief is returned or printed, and
whoever runs the Agent session is a caller. A document range (`WHOLE_DOCUMENT`)
is refused BY NAME rather than guessed at — its material is a document body,
and no field of a capture manifest is blessed by this tree's producer tables as
the body's locator, so a brief that read one would be reading a path it
invented. The named gap is in the refusal, where a caller meets it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent

SCHEMA = "serveros/understanding-brief.v1"

#: Every key a brief may carry, and where its value comes from. Enforced by
#: `no_unsourced` at the point the brief is emitted rather than where it is
#: built: a packet is read by a model that has no way to tell a field it was
#: given from a field the assembler made up.
BRIEF_PRODUCED = {
    "schema": "this module's SCHEMA",
    "source": "the range's own `card`, `manifest`, `from_position` and "
              "`to_position`, in the names `understanding_semantic.commit_key` "
              "compares them under",
    "read": "the range's `content_identity` and the card's `card_digest`, from "
            "the reader — the two values `_check_binding` will compare",
    "range": "the range's `kind` and `revisions`, copied from REVIEW_PRODUCED",
    "card": "the card file the range is about, read here for its own text",
    "material": "the records the range names, from the source's own messages file",
    "reading": "the schema and the field names the validator accepts, read from "
               "`understanding_semantic` itself rather than restated here",
}

CARD_PRODUCED = {
    "path": "the range's `card`, records-root relative, as the reader names it",
    "digest": "the reader's `card_digest` — a digest of the card's text",
    "claims": "the module-written region of the card's fence, or null when it "
              "has no fence to hold one",
    "claims_state": "`understanding_semantic.fence_parts`/`claims_region`, "
                    "reported as MARKED, UNMARKED_LEGACY or NO_FENCE",
}

MATERIAL_PRODUCED = {
    "manifest": "the range's `manifest`, the capture object's key",
    "path": "`understanding_delta.messages_file` — the one derivation of that "
            "path in this tree",
    "state": "counts over the records in the span, from the same parse that "
             "produced them",
    "records": "the records in the span, projected to RECORD_PRODUCED",
}

RECORD_PRODUCED = {
    "position": "the record's `message_position`",
    "content": "the record's `content` — null when the record carries none, "
               "which is not the same as an empty message",
    "updated": "the record's `updated` or `edited` flag, as one boolean",
    "deleted": "the record's `deleted` flag",
}

CLAIMS_STATES = ("MARKED", "UNMARKED_LEGACY", "NO_FENCE")


class Refused(RuntimeError):
    """This module will not hand an Agent material it cannot pin."""


class Malformed(Refused):
    """One file is not what its shape says it is. A fact about the file."""


def _sibling(name: str):
    """A module beside this one, loaded by path — the idiom its siblings use.

    Loaded by path and cached under a fixed name so that a caller which loaded
    the same file gets the SAME module: two copies of `understanding_semantic`
    would be two `Refused` classes, and a `except Refused` written against one
    would not catch the other's refusal.
    """
    if name in sys.modules:
        return sys.modules[name]
    path = _HERE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"serveros_intake_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"serveros_intake_{name}"] = module
    spec.loader.exec_module(module)
    return module


_delta = _sibling("understanding_delta")
_semantic = _sibling("understanding_semantic")


def no_unsourced(mapping: dict, table: dict, where: str) -> dict:
    """Refuse a record carrying a key nothing in `table` produced.

    The same guard the other readers here apply at the point a record is
    EMITTED rather than where it is built, and it matters more for a packet than
    for a report: a brief is the input to a model, and a field with no producer
    is indistinguishable to that model from a measured one.
    """
    unknown = sorted(k for k in mapping if k not in table)
    if unknown:
        raise Refused(
            f"{where} carries keys this module has no producer for: {unknown}. "
            f"Every field of a brief names the record it came from; a field that "
            f"cannot is a value this module invented for a model to read.")
    return mapping


def _flag(record: dict, *names: str) -> bool:
    """Whether any of a record's own revision flags is set.

    `updated` and `edited` are two historical spellings of one fact
    (`understanding_delta.revisions_in_range` reads both), and a packet that
    reported them as two fields would invite a reader to think a message could
    be one without the other.
    """
    return any(bool(record.get(name)) for name in names)


def span_records(messages: Path, low: int, high: int) -> tuple[list[dict] | None, str]:
    """The records a span names, projected to the four fields a reader needs.

    `(None, why)` for a span this module cannot stand behind: no messages file,
    a line that is not JSON, or a span holding no record at all. None of those
    is "an empty span" — a brief for a span with nothing in it would be a packet
    that asks a model to read nothing and calls the answer a reading.
    """
    if not messages.is_file():
        return None, (f"the messages file for {messages.name} is not a file, so "
                      f"the span cannot be handed over as text")
    records: list[dict] = []
    try:
        handle = messages.open(encoding="utf-8")
    except OSError as exc:
        return None, f"the messages file could not be read: {exc}"
    with handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                return None, f"line {number} of the messages file is not JSON"
            if not isinstance(record, dict):
                return None, f"line {number} of the messages file is not an object"
            try:
                position = int(str(record.get("message_position")).strip())
            except (TypeError, ValueError):
                # A record with no position is outside every span, exactly as it
                # is in the digest this packet's identity is taken over.
                continue
            if not low <= position <= high:
                continue
            content = record.get("content")
            records.append(no_unsourced({
                "position": position,
                "content": content if isinstance(content, str) else None,
                "updated": _flag(record, "updated", "edited"),
                "deleted": bool(record.get("deleted")),
            }, RECORD_PRODUCED, f"the material row at position {position}"))
    records.sort(key=lambda row: row["position"])
    if not records:
        return None, (f"no record in the messages file carries a position in "
                      f"{low}–{high}, so the span names nothing to read")
    return records, ""


def _card_half(card_row: dict, records_root: Path) -> dict:
    """The card the range is about, read here for the text a reading is about.

    The digest is the reader's (`card_digest`), not recomputed: a brief that
    took its own digest would be a second reader of one file, and the one that
    drifted would be the one the Agent was told to hand back.
    """
    card = card_row["card"]
    path = Path(records_root) / card
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Refused(
            f"the card {card!r} this range is about cannot be read ({exc}), so "
            f"the material cannot name the text the reading is a change to") from exc
    _, fence, _ = _semantic.fence_parts(text)
    if not fence:
        claims, state = None, "NO_FENCE"
    else:
        region = _semantic.claims_region(fence)
        if region is None:
            # A fence written before the module marked its own lines: every line
            # in it is the module's, which is what makes the distinction a fact
            # rather than a guess about which lines were somebody else's.
            claims, state = fence, "UNMARKED_LEGACY"
        else:
            claims, state = region[1], "MARKED"
    return no_unsourced({
        "path": card,
        "digest": card_row["card_digest"],
        "claims": claims,
        "claims_state": state,
    }, CARD_PRODUCED, f"the card half of the brief for {card!r}")


def brief_for(card_row: dict, row: dict, records_root: Path) -> dict:
    """One review range as the packet a native Agent reads.

    Refused rather than emitted for every shape this module cannot pin: a range
    with no positions (a document's material is a body, not a span), a range
    whose content identity is null — that one has its reason already written by
    the reader (`content_identity_why`) and is copied rather than re-worded — and
    a caller that handed in a review RANGE where a CARD belongs. The last is a
    naming refusal rather than a KeyError: the two halves come from one report
    and look alike, and a module whose contract is refusals should not traceback
    on the caller's field.
    """
    if not isinstance(card_row.get("card"), str) or not card_row.get("card_digest"):
        raise Malformed(
            f"this is not a card row: card={card_row.get('card')!r}, "
            f"card_digest={card_row.get('card_digest')!r}. A reading is pinned to "
            f"the card's OWN digest as well as to the range's content identity, and "
            f"a brief built from a range row would carry the pin of one file "
            f"without ever having read it")
    card = card_row["card"]
    low, high = row.get("from_position"), row.get("to_position")
    if low is None or high is None:
        raise Refused(
            f"the range for {card!r} carries no positions, so its material is a "
            f"document body rather than a span of records. No field of a capture "
            f"manifest is a body locator this tree's producer tables bless, so "
            f"handing that body over would mean reading a path this module "
            f"invented — the range kind is {row.get('kind')!r}")
    identity = row.get("content_identity")
    if not identity:
        raise Refused(
            f"the range for {card!r} carries no content identity, so there is no "
            f"version for a reading to be made against: "
            f"{row.get('content_identity_why') or 'the reader did not say why'}")
    messages = _delta.messages_file(row["manifest"], Path(records_root))
    records, why = span_records(messages, int(low), int(high))
    if records is None:
        raise Refused(
            f"the span {row['manifest']} {low}–{high} cannot be handed over: {why}")
    edited = sum(1 for record in records if record["updated"])
    deleted = sum(1 for record in records if record["deleted"])
    source = {"card": card, "manifest": row["manifest"],
              "from_position": low, "to_position": high}
    return no_unsourced({
        "schema": SCHEMA,
        # The pin, as the validator will compare it: these two blocks are copied
        # into the reading, not paraphrased into it.
        "source": source,
        "read": {"card": card, "content_identity": identity,
                 "card_digest": card_row["card_digest"]},
        "range": {"kind": row.get("kind"),
                  "revisions": {"edited": edited, "deleted": deleted,
                                "records": len(records)}},
        "card": _card_half(card_row, Path(records_root)),
        "material": no_unsourced({
            "manifest": row["manifest"],
            "path": str(messages),
            "state": {"records": len(records), "edited": edited, "deleted": deleted},
            "records": records,
        }, MATERIAL_PRODUCED, "the material half of a brief"),
        # Read from the validator rather than restated: a brief that described a
        # schema the validator does not enforce would be a packet that teaches a
        # model to write readings this tree refuses.
        "reading": {"schema": _semantic.READING_SCHEMA,
                    "claim_fields": list(_semantic.CLAIM_PRODUCED),
                    "kinds": list(_semantic.KINDS),
                    "major_changes": list(_semantic.MAJOR_CHANGES)},
    }, BRIEF_PRODUCED, f"the brief for {card!r} {low}–{high}")


def briefs(report: dict, records_root: Path | str) -> tuple[list[dict], list[dict]]:
    """`(what may be handed to a session, and every range that may not)`.

    Two lists rather than one, because a range this module refuses is NOT the
    same as a range the reader did not propose: the first would otherwise be
    absent from the packet with nothing said, and a caller reading an empty
    packet would conclude every card was up to date. The refused rows carry the
    reader's own coordinates and `brief_for`'s sentence, so the reason a range
    was withheld is in front of whoever asked for it.

    This showed up on the real records checkout, not in a test: a project card
    cites both a chat capture (positions) and a document (a body), and a
    listing that raised on the document would have taken the chat range down
    with it — one unpinnable range making a card look like it had none.
    """
    root = Path(records_root)
    out: list[dict] = []
    refused: list[dict] = []
    for card_row in report.get("cards") or ():
        if not card_row.get("card_digest"):
            # A card the reader could not read has no digest to pin a reading
            # to, which is a fact about the card and not a range to hand over.
            continue
        for row in (card_row.get("judged") or {}).get("review_ranges") or ():
            try:
                out.append(brief_for(card_row, row, root))
            except Refused as exc:
                refused.append({
                    "card": row.get("card"), "manifest": row.get("manifest"),
                    "from_position": row.get("from_position"),
                    "to_position": row.get("to_position"),
                    "kind": row.get("kind"), "why": str(exc),
                })
    return out, refused


def select(items: list[dict], *, refused: list[dict] | None = None,
           card: str | None = None, manifest: str | None = None,
           position: int | None = None) -> list[dict]:
    """The briefs a caller asked for, or a refusal naming what is on offer.

    A selection that matched nothing is refused rather than answered with an
    empty list: "the reader proposes no range for this card" and "this card is
    not in the understanding layer" are different facts, and an empty packet
    returned to an Agent session reads as neither.

    `refused` is the other half of `briefs`. With it, "nothing to hand over"
    splits into the three facts it is made of: the reader proposed nothing, the
    reader proposed ranges and none matched the selection, or the reader
    proposed ranges and this module could not pin any of them.
    """
    kept = [item for item in items
            if (card is None or item["source"]["card"] == card)
            and (manifest is None or item["source"]["manifest"] == manifest)
            and (position is None or item["source"]["from_position"] <= position
                 <= item["source"]["to_position"])]
    if kept:
        return kept
    offered = sorted({f"{item['source']['card']} {item['source']['manifest']} "
                      f"{item['source']['from_position']}–"
                      f"{item['source']['to_position']}" for item in items})
    asked = ", ".join(
        f"{name}={value!r}" for name, value in
        (("card", card), ("manifest", manifest), ("position", position))
        if value is not None)
    if not items and not refused:
        raise Refused(
            f"the reader proposes no range at all, so there is nothing to hand a "
            f"native Agent ({asked}). Either every card is up to date with its "
            f"sources or the sources have not been captured yet — `work "
            f"understanding` says which")
    if not items:
        # Every range the reader proposes is one this module will not pin, and
        # each refusal says why. Reporting this as "no range at all" would tell
        # a caller the layer was up to date when what is wrong is the packet.
        raise Refused(
            f"the reader proposes {len(refused)} range(s) and this module cannot "
            f"pin any of them ({asked}): "
            + "; ".join(f"{row['card']} {row['kind']} — {row['why']}"
                        for row in refused))
    raise Refused(
        f"no range matches {asked}. The ranges on offer are: "
        + ("; ".join(offered) if offered else "(none)"))


def dumps(brief: dict) -> str:
    """Serialise deterministically: sorted keys, indented, one file."""
    return json.dumps(brief, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write(brief: dict, path: Path | str) -> Path:
    """Write one brief atomically, to a path the caller names and this checks.

    Absolute, because an Agent session is handed a file and one written relative
    to whatever directory this process stood in is a file the session cannot
    find; and atomic, because the session must never read half a packet and
    author a reading against it.
    """
    target = Path(path)
    if not target.is_absolute():
        raise Refused(
            f"{target} is not absolute; a brief is the material a session reads, "
            f"and one written relative to this process's working directory is not "
            f"readable by the session it is for")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dumps(brief)
    handle, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=".brief-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return target


def render(brief: dict) -> list[str]:
    """One brief as lines a person can read before handing it to an Agent.

    The material is printed in full: the question this verb answers is exactly
    "what text is the model going to be given", and a rendering that summarised
    it would answer a different one.
    """
    source, material = brief["source"], brief["material"]
    state = material["state"]
    lines = [f"card        {source['card']}",
             f"span        {source['manifest']} "
             f"{source['from_position']}–{source['to_position']}"
             f"   ({brief['range']['kind']})",
             f"identity    {brief['read']['content_identity']}",
             f"baseline    {brief['read']['card_digest']}"
             f"   claims {brief['card']['claims_state']}",
             f"records     {state['records']}   "
             f"edited {state['edited']}  deleted {state['deleted']}",
             ""]
    for record in material["records"]:
        marks = []
        if record["deleted"]:
            marks.append("deleted")
        if record["updated"]:
            marks.append("updated")
        head = f"[{record['position']}]"
        if marks:
            head += f" ({', '.join(marks)})"
        lines.append(f"{head} {record['content'] or ''}")
    return lines
