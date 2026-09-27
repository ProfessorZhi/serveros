#!/usr/bin/env python3
"""§S — the global requirement board, composed from projections that exist.

WHAT THIS IS, AND THE THREE NOUNS IT KEEPS APART
------------------------------------------------
§S states the separation before it states anything else:

    Requirement Source  !=  Work Item  !=  Requirement Board

All three already exist in this fabric, and this module supplies only the
third:

    Requirement Source   `records/projects/<p>/items/<ID>/card.yaml`
                         `source_refs` — Feishu message ranges, wiki pages,
                         understanding documents — plus `aliases`, which is
                         where "REQ4 / 需求4" lives. And upstream of the card,
                         work-engine's `item_candidates`, whose
                         `(domain, external_id, external_revision)` triple is
                         the intake identity for a requirement that has not
                         become an Item yet.
    Work Item            work-engine's store. The authority, and the only one.
    Requirement Board    this file. A projection, and nothing else.

THE BOARD IS DERIVED, SO IT CANNOT DISAGREE WITH THE AUTHORITY
--------------------------------------------------------------
§S asks for a board where a completed item keeps its title and gains a
strikethrough, and where reopening removes the strikethrough. The tempting
implementation is a board that remembers: a `completed` flag it sets, and
clears when somebody tells it the Item was reopened.

This board stores nothing. It reads the Item's state and draws the line, so
the strikethrough is a *rendering of the state* rather than a second copy of
it. Reopening an Item — `workengine item reopen <id> --reason --source`, which
returns it to AVAILABLE — removes the strikethrough by the item no longer
being completed, before anybody updates a board. There is no path by which
this file and the store can come to different conclusions about what is
finished, because there is only one of them holding that fact.

For the same reason there is nothing to preserve. §S says completion keeps
the priority, the title and the history; a derived board keeps all three by
never having dropped them, and "not deleting history" is not a rule this file
follows but a thing it is structurally unable to do.

WHICH ITEMS ARE COMPLETE, AND WHERE THAT COMES FROM
---------------------------------------------------
Not from a list here. `state_machine.reopen()` accepts exactly

    {CANCELLED, CLOSED} -> AVAILABLE

and refuses everything else, so those two states and no others are the ones
the authority considers finished-and-reopenable. `DELIVERED` is deliberately
NOT among them: it sits on the mainline between ACTIVE and CLOSED, and an
Item that has been delivered but not closed is an Item with something left to
do. It stays in the active area with its state visible, which is what the
store says about it.

PRIORITY IS DECLARED AND UNPRODUCED, AND THAT IS THE POINT
----------------------------------------------------------
§S's default display is `P0 P1 P2 P3 P4 已完成`. Nothing in this fabric
carries a priority: work-engine's `items_index` has no such column, the
item-card schema does not require one, and the roadmap has listed "a new
Item-card field — priority / severity do not exist anywhere" as a decision
that belongs to a person since before this round.

So the five levels are rendered empty and marked `UNPRODUCED`, with the reason
on the document, rather than filled with a guess. A board that bucketed by
`kind` or by `state` and called the result P0–P4 would be inventing a
taxonomy, presenting it in a column a reader has been told means priority, and
doing it in the file whose entire job is to be derived from facts. The
`titles_naming_a_level` field below exists for the opposite reason: three real
titles contain "P0", and reporting that the string appears is not the same act
as reading it as a priority — so it is reported, and it never fills a bucket.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

SCHEMA = "serveros/requirement-board.v1"

# Read from the authority, not chosen here. See the module docstring.
BOARD_SCHEMA = "serveros/board.v3"
COMPLETED_STATES = ("CLOSED", "CANCELLED")

DECLARED_LEVELS = ("P0", "P1", "P2", "P3", "P4")
UNCLASSIFIED = "UNCLASSIFIED"
COMPLETED = "COMPLETED"

PRIORITY_BECAUSE = (
    "no priority, severity or P-level field exists anywhere in this fabric — "
    "not in work-engine's items_index, not in the item-card schema's required "
    "keys, and not in this repository. §S's levels are declared here and "
    "rendered empty rather than filled from a proxy: bucketing by kind or by "
    "state and calling the result P0-P4 would be inventing a taxonomy and "
    "presenting it in a column that means priority. Introducing the field is "
    "on the roadmap's list of things that have to be decided by a person"
)


class Refused(Exception):
    """A projection was asked to compose something that is not what it says."""


def _completed(item: dict) -> bool:
    return str(item.get("state") or "") in COMPLETED_STATES


def _latest_run(runs: list[dict]) -> dict | None:
    """The last Run, by run_id.

    Not by timestamp: a Run row's times belong to the store and this module
    reads a projection, where `run_id` is the ordering the producer already
    committed to. Sorting by a field that may be absent would silently reorder
    the board the first time the producer stopped emitting it.
    """
    return sorted(runs, key=lambda r: str(r.get("run_id") or ""))[-1] if runs else None


def _summary(row: dict) -> str:
    """One line, built only from fields that are present.

    A summary that renders an absent fact as a zero is how a board comes to
    say "0 runs" about an Item nobody has looked at. Every clause here is
    conditional on the fact existing.
    """
    parts = [f"{row['state'] or 'UNKNOWN'}"]
    if row.get("outcome"):
        parts.append(str(row["outcome"]))
    if row.get("run_count"):
        latest = row.get("latest_run") or {}
        tail = latest.get("state") or "UNKNOWN"
        if latest.get("verdict"):
            tail = f"{tail}/{latest['verdict']}"
        parts.append(f"{row['run_count']} run(s), last {tail}")
    if row.get("deliverables"):
        parts.append(f"{row['deliverables']} deliverable(s)")
    if row.get("execution_session"):
        parts.append(f"session {row['execution_session']}")
    return " · ".join(parts)


def item_row(item: dict, *, project: str, card: dict | None,
             binding: dict | None, card_known: bool = False) -> dict:
    """One Item, as the board needs it. Every field read, none inferred.

    `card` and `binding` are the two joins this projection makes, and both are
    genuinely optional in the world: an Item may have no card file on this
    node, and most Items have no session bound to them. A missing join is
    reported as a missing join — `source.state` and `execution_session: null` —
    and never as an empty stand-in that reads like a value.

    `source.state` has three values and they are three different facts:

        JOINED      a card was read
        UNREADABLE  a card file is there and this process could not parse it
        NOT_FOUND   no card file is there

    UNREADABLE exists because the first draft of this function had two values
    and a docstring claiming it kept them apart. It did not: an unparseable
    card became `None`, `None` meant NOT_FOUND, and a card sitting on disk with
    a YAML error in it would have been reported as a card that is not there —
    which sends a reader to look for a file they are standing next to.
    """
    activities = item.get("activities") or []
    runs = [run for activity in activities for run in (activity.get("runs") or [])]
    latest = _latest_run(runs)
    item_id = str(item.get("id") or "")
    source_refs = list((card or {}).get("source_refs") or [])
    aliases = list((card or {}).get("aliases") or [])
    if card is not None:
        card_state = "JOINED"
    elif card_known:
        card_state = "UNREADABLE"
    else:
        card_state = "NOT_FOUND"
    row = {
        "item_ref": f"{project}/{item_id}",
        "id": item_id,
        "project": project,
        "title": item.get("title"),
        "kind": item.get("kind"),
        "state": item.get("state"),
        "outcome": item.get("outcome"),
        "priority": None,
        # §S lists the execution session per active item. It comes from the
        # binding W2 introduced, and `null` means what it says: no session has
        # ever been created for this Item, which is the normal case today.
        "execution_session": (binding or {}).get("session_ref"),
        "session_role": (binding or {}).get("session_role"),
        "activities": len(activities),
        "runs": [{"run_id": r.get("run_id"), "state": r.get("state"),
                  "verdict": r.get("verdict")} for r in runs],
        "run_count": len(runs),
        "latest_run": ({"run_id": latest.get("run_id"), "state": latest.get("state"),
                        "verdict": latest.get("verdict")} if latest else None),
        "deliverables": len(item.get("deliverables") or []),
        "source": {
            "card_locator": item.get("card_locator"),
            "state": card_state,
            "source_refs": source_refs,
            "aliases": aliases,
        },
        "candidate_mappings": item.get("candidate_mappings") or [],
        # One field, one fact. A first draft carried `strikethrough` beside it
        # holding the same expression, which is two places for one truth to go
        # wrong — and the renderer reads `completed`, so the copy was never
        # consulted and could only ever have drifted.
        "completed": _completed(item),
    }
    row["summary"] = _summary(row)
    return row


def _levels_named_in_a_title(rows: list[dict]) -> list[dict]:
    """Titles that contain a literal `P0`–`P4`, reported and not used.

    Three real Items name a P-level in their title and nothing else in the
    fabric does. This is evidence for the decision the priority axis is
    waiting on — it says the levels are in use as prose — and it is kept in a
    field of its own so that it cannot be mistaken for the axis being filled.
    The distinction this round keeps paying for is exactly this one: reporting
    that a string occurs is not reading it as a fact.

    It is computed over EVERY row, including completed ones, and that was a
    correction: the first version scanned only the active items, which found
    one of the three and would have handed the decision-maker a third of the
    evidence while reading as a complete list. A count is only evidence if the
    set it counts over is the set the question is about, and the question here
    — "does this fabric express priority in prose" — is about the corpus.
    """
    found = []
    for row in rows:
        title = str(row.get("title") or "")
        for level in DECLARED_LEVELS:
            if level in title:
                found.append({"item_ref": row["item_ref"], "title": title,
                              "literal": level})
                break
    return found


def compose(boards: list[dict], *, cards: dict[str, dict] | None = None,
            bindings: dict[str, dict] | None = None) -> dict:
    """The global board, from the per-project projections and two joins.

    `boards` are `serveros/board.v3` documents as the control plane wrote them.
    A document that is not that schema is refused rather than read hopefully:
    the one thing this module must not do is guess at the shape of a
    projection it did not produce, because every field it would then report
    would be a field it made up.

    Nothing here reads the work-engine store. The store is the authority and it
    is on the other side of a process boundary this repository deliberately
    does not cross — `fleet/mcp-contract.yaml` owns that edge. What this
    composes are projections that were already taken from the authority, which
    is why the result is called a board and not a second answer.
    """
    cards = cards or {}
    bindings = bindings or {}
    rows: list[dict] = []
    seen: dict[str, str] = {}
    projects: list[str] = []
    for document in boards:
        schema = document.get("schema")
        if schema != BOARD_SCHEMA:
            raise Refused(
                f"a document claiming to be a board has schema {schema!r}, not "
                f"{BOARD_SCHEMA!r}. This composes projections the control plane "
                f"wrote; a document of another shape would have to be guessed "
                f"at, and every field guessed is a field reported wrongly")
        project = str(document.get("project") or "")
        projects.append(project)
        for item in document.get("items") or []:
            ref = f"{project}/{item.get('id')}"
            if ref in seen:
                raise Refused(
                    f"{ref} appears in two board documents. An Item has one "
                    f"project; a duplicate is the board reporting one thing "
                    f"twice and looking like two things")
            seen[ref] = project
            row = item_row(item, project=project,
                           card=cards.get(ref),
                           card_known=ref in cards,
                           binding=bindings.get(ref))
            rows.append(row)

    active = [r for r in rows if not r["completed"]]
    completed = [r for r in rows if r["completed"]]
    # §S's default display, in §S's order. The priority buckets are present and
    # empty; see PRIORITY_BECAUSE.
    sections = [{"key": level, "title": level, "kind": "priority", "items": []}
                for level in DECLARED_LEVELS]
    sections.append({"key": UNCLASSIFIED, "title": "未分级",
                     "kind": "unclassified", "items": active})
    sections.append({"key": COMPLETED, "title": "已完成",
                     "kind": "completed", "items": completed})
    revisions = [d.get("generation_source_revision") for d in boards
                 if d.get("generation_source_revision")]
    # Each board document carries `generated_at`, and `board-contract.yaml`
    # says what it is for: "a reader has to be able to tell a fresh board from
    # one generated before the change they are looking for." Composing reads
    # those documents, so their time has to travel with them. Without this the
    # composition's own `generated_at` — the moment it composed — is the only
    # date on the page, and it dates Item state the control plane measured
    # days ago to this second. That is not a rare case here: the contract names
    # the control plane as the writer of `state/boards/`, and on a node where
    # that control plane does not run the snapshot is all this read will ever
    # see. So the times are copied verbatim, per project, uninterpreted — this
    # module reports when the projection was taken, not whether it is recent.
    board_at = {str(d.get("project") or ""): d.get("generated_at")
                for d in boards if d.get("generated_at")}
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "projects": projects,
            "boards": len(boards),
            "generation_source_revision": max(revisions) if revisions else None,
            "board_generated_at": board_at,
        },
        "priority": {
            "producer": None,
            "state": "UNPRODUCED",
            "declared_levels": list(DECLARED_LEVELS),
            "because": PRIORITY_BECAUSE,
            "titles_naming_a_level": _levels_named_in_a_title(rows),
        },
        "completed_states": list(COMPLETED_STATES),
        "sections": sections,
        "counts": {"items": len(rows), "active": len(active),
                   "completed": len(completed),
                   "without_a_card": sum(1 for r in rows
                                         if r["source"]["state"] != "JOINED")},
        "items": sorted(rows, key=lambda r: r["item_ref"]),
    }


def render(document: dict, output_dir: Path | str) -> dict:
    """Write the board. `current.json` and `current.md`, atomically.

    The same pair of files the control plane's per-project renderer writes, for
    the same reason: one machine-readable and one for a person, generated
    together so they cannot describe different generations of the board. The
    directory must be absolute — a projection that landed in whatever
    directory the caller was standing in is a second board nobody knows about.
    """
    output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        raise Refused("board output directory must be absolute")
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "current.json"
    markdown_path = output_dir / "current.md"
    for path, content in ((json_path, json.dumps(document, ensure_ascii=False,
                                                indent=2) + "\n"),
                          (markdown_path, str(markdown(document)))):
        temporary = path.with_name(f".{path.name}.serveros.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    return {"json": str(json_path), "markdown": str(markdown_path),
            "items": document["counts"]["items"]}


def _age_days(now_iso, then_iso) -> int | None:
    """Whole days between two ISO timestamps, or None when that cannot be said.

    Exists for one line of `markdown`, and `None` is one of its answers rather
    than an error: a timestamp this module cannot parse is one it will not turn
    into an age, because an age is a claim about the past and a guessed one
    would be read as a measurement. Naive timestamps are refused for the same
    reason — the difference between two of them is not a duration until someone
    says which zone each was written in.
    """
    try:
        now = datetime.fromisoformat(str(now_iso))
        then = datetime.fromisoformat(str(then_iso))
    except (TypeError, ValueError):
        return None
    if now.tzinfo is None or then.tzinfo is None:
        return None
    return max(0, (now - then).days)


def markdown(document: dict) -> str:
    """The board a person reads.

    `~~title~~` on a completed Item is the strikethrough §S asks for, and it
    is drawn from `row["completed"]` — a rendering of the Item's state and
    nothing else. There is no branch here that sets it, which is why reopening
    removes it: the Item is no longer CLOSED, so the field is false, so the
    tildes are not written.
    """
    lines = ["# 需求总看板", "",
             f"Generated: `{document['generated_at']}`"]
    # The composition's own time says when this page was drawn. It does NOT
    # say when the Item state on it was measured — that came from the board
    # documents, and `board-contract.yaml` carries their `generated_at` so a
    # reader can tell a fresh board from one taken before the change they are
    # looking for. Printing only the first date is what makes a two-week-old
    # snapshot read as today's state, so both dates are printed, on adjacent
    # lines, in that order.
    board_at = ((document.get("source") or {}).get("board_generated_at") or {})
    if board_at:
        stated = []
        for project, at in sorted(board_at.items()):
            age = _age_days(document["generated_at"], at)
            stated.append(f"{project} `{at}`"
                          + (f"（{age} 天前）" if age is not None else ""))
        lines.append(f"看板状态取出时间: {'；'.join(stated)}")
    lines.append(f"Projects: {', '.join(document['source']['projects']) or 'none'}")
    lines.append("")
    priority = document["priority"]
    lines += [f"> priority: **{priority['state']}** — {priority['because']}", ""]
    for section in document["sections"]:
        lines += [f"## {section['title']} ({len(section['items'])})", ""]
        if section["kind"] == "priority" and not section["items"]:
            lines += ["_no Item carries this level; the axis has no producer_", ""]
            continue
        if not section["items"]:
            lines += ["_none_", ""]
            continue
        lines += ["| Item | Title | Project | State | Priority | Session | Runs | Source |",
                  "|---|---|---|---|---|---|---:|---|"]
        for row in section["items"]:
            title = str(row["title"] or "")
            if row["completed"]:
                title = f"~~{title}~~"
            source = row["source"]["aliases"] or []
            lines.append(
                f"| {row['item_ref']} | {title} | {row['project']} | "
                f"{row['state']} | {row['priority'] or 'UNPRODUCED'} | "
                f"{row['execution_session'] or '-'} | {row['run_count']} | "
                f"{', '.join(str(a) for a in source) or '-'} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# The thin disk-touching shell. Everything above is pure.
# ---------------------------------------------------------------------------


def load_boards(boards_root: Path) -> list[dict]:
    """Every `current.json` the control plane has written, one per project.

    Read from disk rather than listed from a constant, because the set of
    projects is the control plane's to decide and a list here would be a
    second, older copy of it. A directory without a board is skipped: the
    namespace may hold a project whose board was never rendered, and that is a
    fact about the project rather than a failure of this read.

    THIS ROOT MUST NOT CONTAIN THIS MODULE'S OWN OUTPUT. The first draft wrote
    the global board to `<boards root>/global/`, where this glob would have
    picked it up on the next run as a board for a project called `global` and
    refused the whole composition over a schema mismatch it had created
    itself. The output lives in its own namespace for that reason and not for
    tidiness — see `STATE_SERVEROS_TOP_LEVEL` in `runtime/paths.py`.
    """
    documents = []
    if not boards_root.is_dir():
        return documents
    for path in sorted(boards_root.glob("*/current.json")):
        documents.append(json.loads(path.read_text(encoding="utf-8")))
    return documents


def load_cards(locators: dict[str, str]) -> dict[str, dict | None]:
    """The Item cards this node can actually read, keyed by `<project>/<id>`.

    A locator whose file is missing is left out of the mapping, and a locator
    whose file is there but does not parse is mapped to `None`. Those are two
    different facts and `item_row` reports them as two different values; the
    first draft collapsed them and would have called a YAML error "no card
    here". `card_locator` is written by the authority and has been seen in both
    absolute and repository-relative form, so the caller resolves it — see
    `intake` in `runtime/cli.py`.
    """
    cards: dict[str, dict | None] = {}
    for ref, path in locators.items():
        candidate = Path(path)
        if not candidate.is_file():
            continue
        try:
            cards[ref] = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        except Exception:
            cards[ref] = None
    return cards


def _bindings_by_item(bindings: dict) -> dict[str, dict]:
    """`item_ref -> the open binding for it`, from the W2 store's records.

    Only OPEN bindings: a closed thread is history, and a board that kept a
    closed session in the `execution session` column would be showing a reader
    who is working on an Item a session that is not. Where two open sessions
    name the same Item — which W2 does not forbid, because a coordinator and an
    execution session are different roles on one Item — the execution one wins,
    since that is the session doing the work.
    """
    chosen: dict[str, dict] = {}
    for ref, record in (bindings or {}).items():
        if str(record.get("state") or "") != "OPEN":
            continue
        item = str(record.get("bound_item_ref") or "").strip()
        if not item:
            continue
        record = dict(record) | {"session_ref": record.get("session_ref") or ref}
        existing = chosen.get(item)
        if existing is None or record.get("session_role") == "execution":
            chosen[item] = record
    return chosen


def build(*, boards_root: Path, card_locator_resolver,
          binding_store: Path | None = None) -> dict:
    """Read, compose, and return the document. Writes nothing.

    `card_locator_resolver` is REQUIRED and has no default, which is not
    tidiness. A first version defaulted it to `None` and skipped every locator
    when it was absent, so a caller who forgot it got a board in which every
    Item read `NOT_FOUND` — "no card file is there" reported for cards sitting
    on disk. That is the same defect `item_row`'s UNREADABLE exists to prevent,
    one layer up: a failure of this process reported as a fact about the world.
    The locator is written by the authority in both absolute and
    repository-relative form, so only the caller knows what it is relative to,
    and the caller must say.
    """
    boards = load_boards(boards_root)
    locators: dict[str, str] = {}
    for document in boards:
        project = document.get("project")
        for item in document.get("items") or []:
            locator = item.get("card_locator")
            if not locator:
                continue
            locators[f"{project}/{item.get('id')}"] = card_locator_resolver(locator)
    bindings: dict = {}
    if binding_store is not None and Path(binding_store).is_file():
        bindings = json.loads(Path(binding_store).read_text(encoding="utf-8")).get("bindings") or {}
    return compose(boards, cards=load_cards(locators),
                   bindings=_bindings_by_item(bindings))
