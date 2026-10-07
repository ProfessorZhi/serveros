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

For the same reason there is nothing of the Item's history to preserve. §S says
completion keeps the priority, the title and the history; a derived board keeps
all three by never having dropped them, and "not deleting history" is not a rule
this file follows but a thing it is structurally unable to do.

THE ONE REGION THAT IS NOT DERIVED
----------------------------------
§5 keeps two rules that pull against each other: the board is regenerated from
facts, and a person's note on it is not a fact this module may drop. So the page
carries one marked region — `HUMAN_BEGIN` … `HUMAN_END` — which `render` reads
out of the page it is about to replace and writes back verbatim, while
everything outside it is rebuilt. It is the only place in this fabric where a
person's own words and a derived projection are the same file, and a
regeneration that dropped them silently would make the board write-only for the
one reader it has. `extract_human` returns the reason when it cannot read the
block, and `render` puts that on stderr, because losing a note is recoverable
and losing it without a word is not.

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


# The hand-written region of the page. `render` carries it across a
# regeneration; everything outside it is rewritten from the facts.
HUMAN_BEGIN = "<!-- human:begin -->"
HUMAN_END = "<!-- human:end -->"
HUMAN_EMPTY = "_（人工批注区：这一段在重新生成时原样保留，可自由编辑）_"


def extract_human(text: str) -> tuple[str, str]:
    """The hand-written region of a page, and why it could not be read.

    §5 asks for two things at once: the board is regenerated from facts, and a
    person's note on it is not a fact this module is allowed to drop. So
    `render` reads the page it is about to replace and carries this block over
    verbatim.

    An unbalanced pair returns `("", why)` rather than the text after the
    opening marker. That matters on the NEXT render: text that was never closed
    would be carried over and then immediately re-emitted with a fresh closing
    marker, so a single missing marker would grow the block once per render.
    Losing a note is the smaller loss, and the caller is given the reason so it
    is not a silent one — the failure this whole function exists to prevent is
    a person's sentence disappearing between two runs.
    """
    began = text.find(HUMAN_BEGIN)
    if began < 0:
        return "", ""
    opened = began + len(HUMAN_BEGIN)
    ended = text.find(HUMAN_END, opened)
    if ended < 0:
        return "", f"{HUMAN_BEGIN} 没有配对的 {HUMAN_END}"
    if text.find(HUMAN_BEGIN, opened) >= 0:
        return "", f"{HUMAN_BEGIN} 出现了不止一次"
    return text[opened:ended].strip("\n"), ""


def render(document: dict, output_dir: Path | str) -> dict:
    """Write the board. `current.json` and `current.md`, atomically.

    The same pair of files the control plane's per-project renderer writes, for
    the same reason: one machine-readable and one for a person, generated
    together so they cannot describe different generations of the board. The
    directory must be absolute — a projection that landed in whatever
    directory the caller was standing in is a second board nobody knows about.

    The page is REPLACED, not appended to, and the one thing that survives the
    replacement is the block between the human markers: this is the only place
    in the fabric where a person's own words and a derived projection are the
    same file, and a regeneration that silently dropped them would make the
    board write-only for its reader.
    """
    output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        raise Refused("board output directory must be absolute")
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "current.json"
    markdown_path = output_dir / "current.md"
    human = ""
    if markdown_path.is_file():
        human, why = extract_human(markdown_path.read_text(encoding="utf-8"))
        if why:
            sys.stderr.write(
                f"serverosctl: {markdown_path} 里的人工批注读不出来（{why}）；"
                f"这一段在这次重新生成里不会保留\n")
    for path, content in ((json_path, json.dumps(document, ensure_ascii=False,
                                                indent=2) + "\n"),
                          (markdown_path, str(markdown(document,
                                                       human=human)))):
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


def _item_block(row: dict) -> list[str]:
    """One Item as a block, which is what a 390-pixel page can carry.

    §5 asks for the board to be read on a phone, and the table this replaced
    had eight columns: at 390 px a reader scrolls sideways per row and loses
    the Item the row is about. The fields are the same ones; what changed is
    that they are lines under a heading instead of cells in a row.

    Every line is conditional on its fact existing. `-` is printed where the
    fabric has nothing, and it is the same `-` the table used, so an Item with
    no session does not read as an Item whose session is the empty string.
    """
    title = str(row.get("title") or "")
    if row["completed"]:
        # Drawn from the state, never stored. Reopening removes the tildes
        # because the Item is no longer CLOSED, not because anyone cleared
        # them. See the module docstring.
        title = f"~~{title}~~"
    lines = [f"#### {row['item_ref']} — {title}", "",
             f"- 状态：{row['summary']}",
             f"- 优先级：{row['priority'] or '待排'}"
             + ("（本 fabric 没有优先级生产者）" if not row["priority"] else ""),
             f"- 会话：{row['execution_session'] or '-'}"
             + (f"（{row['session_role']}）" if row.get("session_role") else "")]
    if row["completed"]:
        outcome = (f" · {row['outcome']}" if row.get("outcome")
                   else "（outcome 未记录）")
        lines.append(f"- 结论：{row['state']}{outcome}")
    source = [str(a) for a in (row["source"]["aliases"]
                               or row["source"]["source_refs"] or [])]
    lines.append(f"- 来源：{', '.join(source) or '-'}")
    lines.append("")
    return lines


def markdown(document: dict, *, human: str = "") -> str:
    """The board a person reads.

    Shaped by §5 rather than by what is easiest to emit: 未完成 as blocks a
    phone can read, 已完成 folded away but keeping its title, its original
    priority and its resolution, and a region at the bottom that belongs to a
    person rather than to this function.

    TWO THINGS THIS DOES NOT DO, both because §5 names a field this fabric has
    no producer for. Per-Item 「目标」and 「下一步」are not rendered: no
    projection, card schema or store column carries either, and a heading
    followed by a guess is worse than a heading that is absent. The reader is
    told once, in the note under 未完成, rather than left to conclude that the
    Items have no next step.

    The priority column is the other half of the same rule read the other way:
    the machine value is `UNPRODUCED`, and the page says 待排 — §5's own word
    for it. One fact, two audiences. Neither is a filled-in bucket.
    """
    lines = ["# 需求总看板", "",
             f"生成：`{document['generated_at']}`"]
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
        lines.append(f"看板状态取出时间：{'；'.join(stated)}")
    lines.append(f"Projects: {', '.join(document['source']['projects']) or 'none'}")
    lines.append("")
    priority = document["priority"]
    # The reason is one sentence a reader needs and a paragraph they do not
    # need twice. At 390 px the paragraph pushed the first Item below the fold
    # on every visit, which is §5's "机器字段/完整日志折叠到证据入口" read as a
    # layout rule: the state stays on the page face, the argument behind it is
    # one tap away. The document keeps the full string -- this is a rendering
    # of it, not a shortening of the fact.
    lines += [f"> 优先级：**{priority['state']}**（待排）—— 本 fabric 没有"
              f"任何优先级生产者，五级因此是空的", "",
              "<details><summary>为什么这五级是空的</summary>", "",
              str(priority["because"]), "", "</details>", ""]
    counts = document["counts"]
    if counts.get("without_a_card"):
        # Said once, here, and not per row. A reader who sees an empty 来源 on
        # twelve rows needs to know that the card is on another machine, not
        # that the Item has no source — those are two different facts and only
        # one of them is a problem with the Item.
        lines += [f"> 卡片：{counts['items']} 行里 {counts['without_a_card']} 行"
                  f"没有可读的 card.yaml，这些行的「来源」为空 —— 这不是"
                  f"「没有来源」，是那张卡不在本机", ""]
    # Split on `key`, not on `kind`. A section's `kind` says what SHAPE its
    # rows are and the completed one's is lowercase `completed`, while the
    # constant is uppercase `COMPLETED` — so the first version of these two
    # lines matched nothing, filed 已完成 back into 未完成, and rendered it as
    # one more unfilled level. The key is the section's name; that is what this
    # is asking about.
    active = [s for s in document["sections"] if s["key"] != COMPLETED]
    completed = next((s for s in document["sections"]
                      if s["key"] == COMPLETED), None)
    unfinished = sum(len(s["items"]) for s in active)
    lines += [f"## 未完成（{unfinished}）", ""]
    # §5 asks each unfinished Item for 目标 / 当前状态 / 下一步 / 来源与证据.
    # Two of the four have no producer anywhere in this fabric, and the note
    # says so once rather than leaving two headings that read as "this Item
    # has no next step". It is conditional on that still being true: the day a
    # producer appears, the sentence has to go by itself.
    if all(not s["items"] for s in active if s["kind"] == "priority"):
        lines += ["> 每一级下面还没有 Item，是因为本 fabric 没有任何优先级生产"
                  "者（见上）；下面按 Item 列出。§5 的「目标」「下一步」两栏"
                  "同样没有生产者，因此不渲染。", ""]
    for section in active:
        lines += [f"### {section['title']}（{len(section['items'])}）", ""]
        if section["kind"] == "priority":
            lines += ["_无 Item 带这一级；该轴没有生产者_", ""]
            continue
        for row in section["items"]:
            lines += _item_block(row)
    if completed is not None:
        lines += [f"## 已完成（{len(completed['items'])}）", ""]
        if not completed["items"]:
            lines += ["_无_", ""]
        else:
            # §5: 默认折叠. A completed Item is history, and history that
            # takes the top of the page is history that buries the work. The
            # summary says what unfolding costs so the fold is a choice.
            lines += ["<details>",
                      f"<summary>展开 {len(completed['items'])} 项"
                      f"（删除线标题 = 权威状态为 "
                      f"{'/'.join(document['completed_states'])}；重新打开会"
                      f"由状态本身去掉删除线）</summary>", ""]
            for row in completed["items"]:
                lines += _item_block(row)
            lines += ["</details>", ""]
    lines += [HUMAN_BEGIN, (human.strip("\n") if human.strip() else HUMAN_EMPTY),
              HUMAN_END, ""]
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
