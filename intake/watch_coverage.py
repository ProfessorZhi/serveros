"""The follow list, and whether the capture tree is actually keeping up with it.

WHY THIS MODULE EXISTS
----------------------
`docs/CC-EXECUTION-PLAN.md` P2 has a 黑名单 row: `records/information/raw/
DRIFT_WATCHLIST.txt` names the Feishu documents the drift collector re-fetches
on every session start. On 2026-09-22 `grep -rn "DRIFT_WATCHLIST\\|drift-sync-
state" --include=*.py` over the fabric tree returned **nothing**. The collector
reads its own watchlist (`drift_sync.py:71 load_watchlist()`) and its own
debounce state, and the ServerOS side had no reader for either — so the one
question this list exists to answer, "which documents am I following, and are
they still being re-checked", had no surface at all.

`intake/capture_coverage.py` is the neighbouring reader and reads one half of it
already: it reports each manifest's `last_verified_at` as `RECORDED`/
`NOT_RECORDED`. What it cannot answer is the join against the *demanded* list —
a document can have no capture at all, or two, or a manifest nobody can parse,
and every one of those is invisible from inside a manifest walk because the
manifest is exactly what is missing.

WHAT IT REFUSES TO DO
---------------------
It does not read a clock. "Overdue" needs a threshold, and a threshold is a
policy somebody authors. `--before YYYY-MM-DD` is that input; without it the
report says the question was not asked rather than answering it against today.

It does not treat `.drift-sync-state.json` as a verification record. That file is
the collector's **commit debounce**: `drift_sync.py:246` resets the entry on
every drift round "无论 commit 是否成功", so it says when this link last *tried
to commit*, not when its content was last checked. The live tree makes the
distinction concrete — the state holds `2026-09-15T00:05:53` and the manifest
holds `last_verified_at: 2026-09-16`. Those are two facts about two different
things, and a reader that subtracted them would invent a contradiction.

It does not apply the collector's builtin fallback as if it were authored.
`load_watchlist` returns `["wiki-litellm-work-doc"]` when the file is absent. A
missing file is reported as missing, with the fallback named as the collector's
own default, because "one document is followed" and "the list of documents we
follow is gone" are not the same fact.

It does not quietly drop a malformed line. The collector skips a blank line and
a `#` comment and takes everything else verbatim; a line that cannot be a
link_id is counted and shown, because dropping it would make the follow list
read smaller than it is.

It does not resolve a conflict. Two captures under one `link_id` make
`drift_sync.locate()` return `None`, which means the watch is **silently dead** —
logged to stderr and skipped. That is the class this reader exists to surface,
and picking one of the two hits would hide it.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

SCHEMA = "serveros/watch-coverage.v1"

# The records-repo layout this module reads. `records/` is the workspace root's
# own directory and `information/raw` is its internal path, so neither is a
# declared root in the root contract — the caller supplies the records root the
# same way `intake/capture_coverage` and `intake/workchain` do. `feishu/chats`
# is where `drift_sync.locate()` scans, and it scans `chats` rather than
# `people` on purpose: a group-chat link that only `people` were walked would
# fall silently out of drift monitoring.
RAW_SEGMENTS = ("information", "raw")
CHATS_SEGMENTS = ("feishu", "chats")
WATCHLIST_NAME = "DRIFT_WATCHLIST.txt"
STATE_NAME = ".drift-sync-state.json"

# `drift_sync.DEFAULT_WATCHLIST`'s own fallback, copied rather than imported:
# this module reads the collector's *records*, and importing the collector would
# make the reader stop working the day the collector moves. Reported as the
# collector's default, never as the authored list.
COLLECTOR_BUILTIN_FALLBACK = ("wiki-litellm-work-doc",)

# How a link_id got onto the list. The two are different facts about the list's
# authorship and only one of them is somebody's decision.
LISTED_STATES = ("AUTHORED", "COLLECTOR_FALLBACK")

# What the capture tree holds for one demanded link_id.
WATCH_STATES = (
    "RESOLVED",        # exactly one capture, manifest parses
    "NO_CAPTURE",      # nothing under this link_id — the watch can never fire
    "AMBIGUOUS",       # two or more captures — locate() skips it, so it is dead
    "UNREADABLE",      # the capture is there and its manifest cannot be parsed
)

VERIFIED_STATES = ("RECORDED", "NOT_RECORDED")
DEBOUNCE_STATES = ("RECORDED", "NEVER_COMMITTED")
DRIFT_STATES = ("NEVER_DRIFTED", "DRIFTED", "UNRECORDED")
OVERDUE_STATES = ("OVERDUE", "NOT_OVERDUE", "UNDECIDABLE")

# Every key this module may put in a watch row, and the record it comes from. A
# key with no producer cannot be emitted: `row()` refuses one.
WATCH_PRODUCED = {
    "link_id": "the watchlist line itself",
    "listed": "which list named it — the file, or the collector's builtin fallback",
    "state": "the capture tree: how many `links/<link_id>/manifest.json` are under it",
    "why": "the resolution's own reason — what is missing, conflicting, or "
           "unparseable when the state is not RESOLVED",
    "captures": "the capture paths found, relative to the chats root",
    "title": "the manifest's `title`",
    "url": "the manifest's `url`",
    "type": "the manifest's `type` — wiki / docx / repo",
    "verified": "the manifest's `last_verified_at`, read as capture_coverage reads it",
    "drift": "`content_sha256` against `prev_content_sha256` on the manifest",
    "debounce": "`.drift-sync-state.json[link_id]` — the collector's commit debounce",
    "overdue": "`verified.at` against the caller's `--before` cutoff",
}

# A `YYYY-MM-DD` prefix is what both timestamp forms share: `last_verified_at` is
# a bare date on a document capture and a datetime on a chat capture, and the
# question "was this re-checked before that day" is a question about the day.
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


class Refused(RuntimeError):
    """This reader will not answer, and guessing is not an option.

    Raised for a defect in the READER — an emitted key no producer table names,
    a cutoff that is not a date. It is deliberately not the exception for a file
    this reader cannot parse: the two report different things, and swallowing
    them together would attribute a reader's bug to a record.
    """


class Malformed(Refused):
    """A file is not what this reader reads. A fact about the file, not the reader."""


def raw_root(records_root: Path) -> Path:
    """`<records_root>/information/raw` — composed here so callers cannot differ."""
    return Path(records_root).joinpath(*RAW_SEGMENTS)


def chats_root(records_root: Path) -> Path:
    """`<records_root>/information/raw/feishu/chats` — `drift_sync.locate()`'s own root."""
    return raw_root(records_root).joinpath(*CHATS_SEGMENTS)


def watchlist_path(records_root: Path, override: str = "") -> Path:
    """The list `drift_sync` would read, including its `DRIFT_WATCHLIST` override.

    The override is part of the collector's contract and is honoured here for
    the same reason the file itself is: this reader reports what the collector
    was actually pointed at, and a reader that ignored the override would
    describe a run that did not happen.
    """
    if override:
        return Path(override)
    return raw_root(records_root) / WATCHLIST_NAME


def read_watchlist(path: Path) -> dict:
    """The demanded link_ids, and how this list came to be.

    Returns `{entries, listed, malformed, why}`. A line that is neither blank
    nor a comment is an entry, exactly as the collector takes it; one that
    carries whitespace cannot be a link_id (ids are directory names) and is
    reported in `malformed` rather than dropped, so the list cannot read
    smaller than it is.
    """
    if not path.exists():
        return {
            "entries": list(COLLECTOR_BUILTIN_FALLBACK),
            "listed": "COLLECTOR_FALLBACK",
            "malformed": [],
            "path": str(path),
            "why": f"{path} is absent. `drift_sync.load_watchlist()` falls back "
                   f"to its own builtin default when the file is missing, so the "
                   f"entries below are the COLLECTOR'S DEFAULT and not a list "
                   f"anybody authored — 'one document is followed' and 'the "
                   f"follow list is gone' are different facts",
        }
    entries: list[str] = []
    malformed: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if re.search(r"\s", line):
            malformed.append(line)
            continue
        entries.append(line)
    return {
        "entries": entries,
        "listed": "AUTHORED",
        "malformed": malformed,
        "path": str(path),
        "why": "",
    }


def captures_for(records_root: Path, link_id: str) -> list[Path]:
    """Every `links/<link_id>/manifest.json` under the chats root, sorted.

    The glob is the collector's own (`drift_sync.locate()` scans
    `<chats>/*/*/*/links/*/manifest.json` and matches on the parent directory
    name) so that this reader and the collector cannot disagree about which
    captures a link_id has. Sorted, so two runs over one tree agree on order.
    """
    root = chats_root(records_root)
    if not root.is_dir():
        return []
    found = [mf for mf in sorted(root.glob("*/*/*/links/*/manifest.json"))
             if mf.parent.name == link_id]
    return sorted(found)


def read_state(path: Path) -> dict:
    """The collector's commit-debounce state, as written. `{}` when never written.

    Absent is normal and is not an error: the collector writes nothing until a
    link has been through a drift round. Present-but-unparseable is a fact about
    the file and raises `Malformed` — the debounce would silently reset, and a
    reader that answered `{}` there would report a missing gate as a fresh one.
    """
    if not path.exists():
        return {"entries": {}, "path": str(path), "why": "never written"}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Malformed(f"{path} is not a JSON object: {exc}") from exc
    if not isinstance(document, dict):
        raise Malformed(f"{path} holds {type(document).__name__}, not an object")
    return {"entries": document, "path": str(path), "why": ""}


def day_of(text) -> str:
    """The leading `YYYY-MM-DD` of a timestamp, or `""` when there is not one."""
    if not isinstance(text, str):
        return ""
    match = DAY.match(text)
    return match.group(0) if match else ""


def no_unsourced(mapping: dict, table: dict, where: str) -> dict:
    """Refuse a key the producer table does not name.

    Applied where a row is EMITTED rather than where it is built, so a field
    added to the builder and forgotten here is a refusal and not a silent extra
    column — the same guard `intake/capture_coverage` and
    `intake/understanding_delta` apply to their own rows.
    """
    unknown = sorted(set(mapping) - set(table))
    if unknown:
        raise Refused(
            f"{where} carries keys this module has no producer for: {unknown}. "
            f"Every emitted field names the record it came from; a field that "
            f"cannot is a value somebody invented.")
    return mapping


def row(link_id: str, listed: str, captures: list[Path], records_root: Path,
        manifest: dict, unreadable: str, state: dict, cutoff: str) -> dict:
    """One demanded link_id, and what the records say about it."""
    if unreadable:
        where, verified, drift = "UNREADABLE", None, None
    elif not captures:
        where, verified, drift = "NO_CAPTURE", None, None
    elif len(captures) > 1:
        where, verified, drift = "AMBIGUOUS", None, None
    else:
        where, verified, drift = "RESOLVED", manifest, manifest

    chats = chats_root(records_root)
    relative = []
    for path in captures:
        try:
            relative.append(str(path.parent.relative_to(chats)))
        except ValueError:
            relative.append(str(path.parent))

    if verified is not None:
        at = verified.get("last_verified_at")
        if not at:
            # A chat capture keeps it under `sync`; the two are both read and
            # neither is inferred from the other (`capture_coverage.py:433`).
            sync = verified.get("sync")
            at = sync.get("last_verified_at") if isinstance(sync, dict) else None
        verified_block = {
            "state": "RECORDED" if at else "NOT_RECORDED",
            "at": at or "",
            "why": "" if at else
                   "no `last_verified_at`: nothing on this manifest records an "
                   "independent re-check, so the watch has no idea how old this "
                   "capture is",
        }
    else:
        verified_block = {"state": "NOT_RECORDED", "at": "", "why":
                          "no readable manifest, so there is no re-check date to read"}

    if drift is not None:
        now_digest = drift.get("content_sha256")
        prev_digest = drift.get("prev_content_sha256")
        if not now_digest:
            drift_block = {"state": "UNRECORDED", "content_sha256": "",
                           "prev_content_sha256": "", "why":
                           "the manifest carries no `content_sha256`, so no "
                           "drift can be decided from it"}
        elif not prev_digest:
            drift_block = {"state": "NEVER_DRIFTED", "content_sha256": now_digest,
                           "prev_content_sha256": "", "why":
                           "no `prev_content_sha256`: the capture has never been "
                           "re-fetched with different content"}
        else:
            drift_block = {
                "state": "DRIFTED" if prev_digest != now_digest else "NEVER_DRIFTED",
                "content_sha256": now_digest, "prev_content_sha256": prev_digest,
                "why": "" if prev_digest != now_digest else
                       "`prev_content_sha256` equals `content_sha256`: a re-fetch "
                       "happened and the body did not change",
            }
    else:
        drift_block = {"state": "UNRECORDED", "content_sha256": "",
                       "prev_content_sha256": "", "why":
                       "no readable manifest, so there is no digest to compare"}

    debounce_at = state.get(link_id, "")
    debounce_block = {
        "state": "RECORDED" if debounce_at else "NEVER_COMMITTED",
        "at": debounce_at if isinstance(debounce_at, str) else "",
        "why": "" if debounce_at else
               "no entry in the collector's state: this link has never been "
               "through a drift round on this node, so its debounce gate is "
               "not set — which is not the same as the gate saying 'go'",
    }

    if not cutoff:
        overdue_block = {
            "asked": False, "cutoff": "", "state": "UNDECIDABLE",
            "why": "no `--before` cutoff was given and this reader does not read "
                   "a clock: 'overdue' is a threshold, and a threshold is a "
                   "policy somebody authors",
        }
    else:
        day = day_of(verified_block["at"])
        if not day:
            overdue_block = {
                "asked": True, "cutoff": cutoff, "state": "UNDECIDABLE",
                "why": f"no `YYYY-MM-DD` on this capture's re-check record, so it "
                       f"cannot be placed against {cutoff} without inventing a date",
            }
        else:
            overdue_block = {
                "asked": True, "cutoff": cutoff,
                "state": "OVERDUE" if day < cutoff else "NOT_OVERDUE",
                "why": f"last re-check {day}, cutoff {cutoff}",
            }

    if where == "AMBIGUOUS":
        ambiguity = (f"{len(captures)} captures share the link_id {link_id!r}; "
                     f"`drift_sync.locate()` returns None on a conflict and the "
                     f"caller logs and skips, so this watch is dead until one is "
                     f"removed. Picking one here would hide exactly that.")
    elif where == "NO_CAPTURE":
        ambiguity = (f"nothing under links/{link_id}/ — the collector cannot "
                     f"re-fetch a link it has never captured, so this watch can "
                     f"never fire")
    elif where == "UNREADABLE":
        ambiguity = unreadable
    else:
        ambiguity = ""

    return no_unsourced({
        "link_id": link_id,
        "listed": listed,
        "state": where,
        "why": ambiguity,
        "captures": relative,
        "title": (manifest or {}).get("title", ""),
        "url": (manifest or {}).get("url", ""),
        "type": (manifest or {}).get("type", ""),
        "verified": verified_block,
        "drift": drift_block,
        "debounce": debounce_block,
        "overdue": overdue_block,
    }, WATCH_PRODUCED, f"the row for {link_id!r}")


def read_root(records_root: Path, watchlist: str = "", before: str = "") -> dict:
    """Every demanded link_id against the capture tree. Reads; never writes.

    `watchlist` has an override because the collector has one (`DRIFT_WATCHLIST`,
    documented in `drift_sync.py`'s own usage block) and this reader reports what
    the collector was actually pointed at. The debounce state has **no** override
    for the same reason: `STATE_FILE` is fixed in the collector, so a caller able
    to point this reader at some other state file could make it describe a run
    that never happened.
    """
    records_root = Path(records_root)
    if not records_root.is_absolute():
        raise Refused(f"the records root must be absolute, got {records_root}")
    if before and not DAY.fullmatch(before):
        raise Refused(
            f"--before must be a YYYY-MM-DD date, got {before!r}. A threshold "
            f"that is not a day cannot be compared against a day.")
    raw = raw_root(records_root)
    if not raw.is_dir():
        raise Refused(f"no capture root at {raw}: there is no tree to read")

    listing = read_watchlist(watchlist_path(records_root, watchlist))
    state = read_state(raw / STATE_NAME)

    rows = []
    for link_id in listing["entries"]:
        paths = captures_for(records_root, link_id)
        manifest: dict = {}
        unreadable = ""
        if len(paths) == 1:
            try:
                document = json.loads(paths[0].read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                unreadable = f"{paths[0]} cannot be parsed: {exc}; `locate()` logs and skips it, so the watch is dead"
            else:
                if not isinstance(document, dict):
                    unreadable = (f"{paths[0]} holds {type(document).__name__}, "
                                  f"not a manifest object; `locate()` would skip it")
                else:
                    manifest = document
        rows.append(row(link_id, listing["listed"], paths, records_root,
                        manifest, unreadable, state["entries"], before))

    watched = set(listing["entries"])
    unwatched = sorted(k for k in state["entries"] if k not in watched)
    return {
        "schema": SCHEMA,
        "root": str(records_root),
        "watchlist": {
            "path": listing["path"],
            "listed": listing["listed"],
            "why": listing["why"],
            "malformed": listing["malformed"],
        },
        "state": {"path": state["path"], "why": state["why"]},
        "cutoff": before,
        "watches": rows,
        "state_without_watch": unwatched,
        "summary": summarize(rows, listing, unwatched),
    }


def summarize(rows: list[dict], listing: dict, unwatched: list[str]) -> dict:
    """Counts per question, never one number for the list.

    A follow list can be entirely resolved and still have every capture with no
    re-check record; one `ok` for that object would be a claim about a question
    nobody asked.
    """
    by_state: dict = {}
    by_verified: dict = {}
    by_drift: dict = {}
    by_debounce: dict = {}
    by_overdue: dict = {}
    for row in rows:
        by_state[row["state"]] = by_state.get(row["state"], 0) + 1
        by_verified[row["verified"]["state"]] = \
            by_verified.get(row["verified"]["state"], 0) + 1
        by_drift[row["drift"]["state"]] = by_drift.get(row["drift"]["state"], 0) + 1
        by_debounce[row["debounce"]["state"]] = \
            by_debounce.get(row["debounce"]["state"], 0) + 1
        if row["overdue"]["asked"]:
            by_overdue[row["overdue"]["state"]] = \
                by_overdue.get(row["overdue"]["state"], 0) + 1
    return {
        "denominator": len(rows),
        "listed": listing["listed"],
        "by_state": by_state,
        "by_verified": by_verified,
        "by_drift": by_drift,
        "by_debounce": by_debounce,
        "overdue_asked": any(row["overdue"]["asked"] for row in rows),
        "by_overdue": by_overdue,
        "malformed": len(listing["malformed"]),
        "state_without_watch": len(unwatched),
    }
