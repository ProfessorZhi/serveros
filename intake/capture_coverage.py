"""What the collectors were asked to capture, and what actually landed.

WHY THIS MODULE EXISTS
----------------------
`docs/CC-EXECUTION-PLAN.md` P1 requires the shared model to express
"capture policy 与 materialized coverage 分开" — the declared scope of a capture
and the coverage it achieved are two facts, and a reader that merges them into
one `status` field cannot tell a chat that was asked for 40 messages and got 40
from one that was asked for everything and stopped at page one.

`records/information/raw/` holds 68 manifests written by five collectors, and on
2026-09-22 `grep -rn "pagination_complete\\|msg_count" --include=*.py` over the
fabric tree returned **nothing**: the collectors wrote a coverage record that no
ServerOS-side reader has ever opened. `intake/workchain.py` does report a feishu
surface count, but it counts the *authored* `sources.yaml` — the declared side.
This module is the materialized side, and the pair is only readable when both
halves exist and are named as halves.

WHAT IT REFUSES TO DO
---------------------
It does not collapse the components into a verdict. A chat manifest can be fully
paginated and still have three attachments declared pending; a link manifest can
have a body and a truncated digest. One number for that object would be a claim
about a question nobody asked. Each component is stated separately with the
facts it was decided from, and `summarize` counts per component rather than
per object.

It does not turn prose into a field. Six manifests carry a `files.pending`
sentence and six carry a `links.status` sentence — the collector's own words,
which do assert coverage. They are reported verbatim under `unstructured` and
labelled as prose, because a reader who needs them should see exactly what was
written. The long `note` on every manifest is not projected at all: it is a
narrative, and a narrative that looks like a field is worse than no field.

It does not read a clock. `last_verified_at` is reported as the collector wrote
it. How old that is, is a staleness question with its own threshold, and
answering it here would silently make this module the authority on freshness.

It does not guess a policy. 41 of the 47 chat manifests carry no `sync.mode`: the
collector never recorded which mode it ran. That is `undeclared` with the reason,
never a default.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SCHEMA = "serveros/capture-coverage.v1"

# The records-repo layout this module reads, named once. `records/` is the
# workspace root's own directory and `information/raw` is its internal path, so
# neither is a declared root in the root contract — the caller supplies the
# records root the same way `intake/workchain` does and these segments are
# appended.
RAW_SEGMENTS = ("information", "raw")
CAPTURE_SEGMENTS = ("feishu", "chats")

# The shape of a manifest, decided by the keys it carries and never by where it
# sits. `role` comes from the path and is reported beside it, so the two can be
# seen to disagree rather than one silently standing in for the other.
#
# `web_capture` is a page fetched from outside this workspace by
# `docs/EXTERNAL-REFERENCE-LIBRARY.md`. It is a shape and not a role because the
# question it answers is different: a chat capture's completeness is about a
# message stream and a document capture's about a body, while a web capture has
# neither of those denominators and its own (`response`, `materials`).
SHAPES = ("chat_capture", "document_capture", "web_capture", "UNREADABLE",
          "UNKNOWN_SHAPE")
ROLES = ("chat", "link", "web", "UNKNOWN_ROLE")

COMPONENT_STATES = ("COMPLETE", "INCOMPLETE", "UNDECIDABLE")

# Every key this module may put in `capture`, and the manifest field it comes
# from. A value of `None` would mean a key with no producer, which is why there
# is no such entry: `_capture_half` raises on a key that is not here.
CAPTURE_PRODUCED = {
    "mode": "sync.mode — the mode the collector recorded it ran",
    "method": "sync.method — the verbatim command or API call, on a chat "
              "capture; fetch.captured_by on a web capture, where the same "
              "question is asked of a request instead of a run",
    "target_type": "type — a document capture's own kind (wiki/docx/repo)",
    "target_url": "url — what a document capture was pointed at",
    "source_message_id": "source_message_id — the message the link came from",
    "requested_url": "source.requested_url — what a web capture was pointed at",
}

# Every key this module may put in `coverage.components[*].facts`.
COVERAGE_PRODUCED = {
    "msg_count": "sync.msg_count — messages written",
    "remote_unique_message_count": "sync.remote_unique_message_count — the "
                                   "source's own count, the only independent "
                                   "denominator on this node",
    "pagination_complete": "sync.pagination_complete",
    "fetched_page_count": "sync.fetched_page_count",
    "earliest_create_time": "sync.earliest_create_time",
    "latest_create_time": "sync.latest_create_time",
    "last_message_id": "sync.last_message_id",
    "content_sha256": "content_sha256 — the body's digest as written, on a "
                      "document capture; fetch.sha256 on a web capture",
    "content_sha256_chars": "len(content_sha256) — a digest shorter than 64 hex "
                            "characters is a label and not a content address",
    "fetched_at": "fetched_at, on a document capture; fetch.at on a web capture",
    "http_status": "fetch.http_status — what the server answered",
    "final_url": "source.final_url — where the request ended up",
    "redirected": "source.redirected — whether it ended up somewhere else",
    "materials_inventoried": "materials.inventoried — whether the collector "
                             "looked for the page's own materials at all",
    "materials_count": "materials.count — the materials it recorded, which is "
                       "only a count when `materials_inventoried` says it "
                       "looked",
    "media_count": "media_count — the count the collector declared",
    "media_listed": "len(media) — the entries it actually wrote",
    "backfilled": "files.backfilled — attachments materialized",
    "count": "links.count — link expansions declared",
    "expanded": "links.expanded — link expansions written",
    "revisions_checked_at": "sync.revisions_checked_at — the date the collector "
                            "last compared the recorded rows against the source",
    "revisions_compared": "sync.revisions_compared — how many recorded messages "
                          "that comparison actually reached",
    "revision_count": "sync.revision_count — edits the source reported, or that "
                      "the comparison found in the redacted text",
    "retraction_count": "sync.retraction_count — messages the source now reports "
                        "as deleted",
    "restoration_count": "sync.restoration_count — messages this archive recorded "
                         "as deleted that the source no longer reports as deleted",
}

# A digest of full length can be re-checked against the source; a truncated one
# cannot, and the difference is a fact about the record rather than a style.
FULL_DIGEST_CHARS = 64


class Refused(RuntimeError):
    """This reader will not answer, and guessing is not an option.

    Raised for a defect in the READER — an emitted key no producer table names,
    an absent root. It is deliberately not the exception for a file this reader
    cannot parse: swallowing the two together would report a manifest as
    unreadable because the reader had a bug, which is the same misattribution
    the components exist to prevent, one level up.
    """


class Malformed(Refused):
    """One manifest is not a coverage record. A fact about the file, not the reader."""


def raw_root(records_root: Path) -> Path:
    """`<records_root>/information/raw` — composed here so callers cannot differ."""
    root = Path(records_root).joinpath(*RAW_SEGMENTS)
    return root


def walk_manifests(root: Path) -> list[Path]:
    """Every `manifest.json` under the capture root, sorted, as paths.

    Sorted so two runs against one tree produce the same order; a rollup whose
    row order depends on the filesystem's is a rollup two readers can disagree
    about without either being wrong.
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        if "manifest.json" in filenames:
            found.append(Path(dirpath) / "manifest.json")
    return sorted(found)


def role_of(path: Path, root: Path) -> str:
    """Whether this manifest is a chat's own, a link expanded from a chat, or a
    page fetched from outside this workspace.

    Read from the path, and reported beside the shape rather than used to pick
    it: `links/<slug>/manifest.json` sits two levels below a chat, which is the
    capture layout's own structure and not an inference about the content. `web`
    is the same kind of statement one level up — the first segment says which
    plane the capture belongs to, which is all a path can say.
    """
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return "UNKNOWN_ROLE"
    if parts and parts[0] == "web":
        return "web"
    return "link" if "links" in parts else "chat"


def shape_of(document: dict) -> str:
    """The family a manifest belongs to, from its keys alone.

    The web branch is last on purpose: it is the newest shape, and asking it
    last means no manifest that already read as a chat or a document capture
    reads as anything else today. A classification changed by adding a case is
    a reading moved by an edit.
    """
    if "chat" in document and "sync" in document:
        return "chat_capture"
    if "url" in document and "content_sha256" in document:
        return "document_capture"
    if "source" in document and "fetch" in document:
        return "web_capture"
    return "UNKNOWN_SHAPE"


def _require_mapping(value, what: str) -> dict:
    """`value` if it is a mapping, else a refusal naming what it actually is."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise Malformed(
            f"{what} is {type(value).__name__}, not an object; a coverage record "
            f"that cannot be read as fields is not a coverage record")
    return value


def _capture_half(shape: str, document: dict) -> tuple[dict, list[dict]]:
    """The declared half: what the collector was asked to do, and what it did not say.

    A field the manifest does not carry is *absent* from the returned mapping and
    named in `undeclared` with the reason. It is never present-and-empty: an
    empty string reads as a value, and "the collector never recorded a mode" is
    not a mode.
    """
    source: dict = {}
    if shape == "chat_capture":
        sync = _require_mapping(document.get("sync"), "`sync`")
        source = {"mode": sync.get("mode"), "method": sync.get("method")}
    elif shape == "web_capture":
        # A web capture has no run to record a mode for: it was pointed at one
        # address, once. What it was asked is the request; where that request
        # ended up is a fact about the answer and is reported by `response`.
        web_source = _require_mapping(document.get("source"), "`source`")
        fetch = _require_mapping(document.get("fetch"), "`fetch`")
        source = {"requested_url": web_source.get("requested_url"),
                  "method": fetch.get("captured_by")}
    else:
        source = {
            "target_type": document.get("type"),
            "target_url": document.get("url"),
            "source_message_id": document.get("source_message_id"),
        }
    declared = {key: value for key, value in source.items() if value is not None}
    undeclared = [
        {
            "field": key,
            "produced_by": CAPTURE_PRODUCED[key].split(" — ")[0],
            "why": f"the manifest carries no {CAPTURE_PRODUCED[key].split(' — ')[0]}; "
                   f"the collector did not record this, and the reader will not "
                   f"supply a default for it",
        }
        for key in source
        if source[key] is None
    ]
    return declared, undeclared


def no_unsourced(mapping: dict, table: dict, where: str) -> dict:
    """`mapping` if every key is in `table`, else a refusal naming the orphans.

    Applied where a record is EMITTED rather than where each key is produced.
    A guard only at the producer protects the producer: a second code path, or a
    caller that assembles the mapping itself, would reach the record unchecked,
    and the record is the thing that gets read. The table is what makes the
    check real — `PRODUCED`-style maps are worthless as documentation and
    load-bearing as an assertion.
    """
    unexpected = sorted(set(mapping) - set(table))
    if unexpected:
        raise Refused(
            f"this reader would emit {', '.join(f'`{where}.{key}`' for key in unexpected)}, "
            f"which the producer table does not name a source for; add the "
            f"producer or drop the field")
    return mapping


def _facts(*pairs, table: dict = COVERAGE_PRODUCED) -> dict:
    """The named pairs, keeping only those the manifest actually carries.

    `False` and `0` are facts and are kept; only `None` — the manifest does not
    carry it — is dropped, so `pagination_complete: false` cannot disappear into
    the same absence as a manifest that never recorded it.
    """
    out: dict = {}
    for key, value in pairs:
        if value is not None:
            out[key] = value
    return no_unsourced(out, table, "coverage")


def _number(value):
    """An int, or `None` when the manifest carries something else.

    Counts from these manifests are ints; a string `"43"` would compare unequal
    to `43` and produce a false DIVERGENT, so a non-int is treated as absent and
    reported by the component as undecidable rather than as a disagreement.
    """
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _revisions_component(sync: dict, written) -> dict:
    """Whether this archive still matches the source about the text it holds.

    The component exists because of a silent failure this reader could not see
    before it. The collector deduped on `message_id`, and the source answers an
    edited message with the SAME id and new text -- so `new` came out empty, the
    archive went on asserting the pre-edit wording, and nothing anywhere said it
    had moved. `records/information/raw/README.md:29` is the rule it broke
    ("源编辑/撤回保留事件关系"), and the upstream side of this component is the
    collector's `messages.revisions.jsonl`.

    Three states, and the distinction between the first two is the whole point:

    COMPLETE      the manifest states a comparison ran and reached rows. Not
                  "nothing was edited" -- that is `revision_count: 0` beside a
                  non-zero denominator, which is a MEASURED zero. A capture where
                  the source was re-read and had not moved is as complete as one
                  where it had.
    INCOMPLETE    the comparison claims to have reached nothing while the
                  manifest says this capture holds messages. The run happened and
                  its denominator is empty, which is a shortfall rather than an
                  absence to report.
    UNDECIDABLE   no `revisions_checked_at`: this capture never compared. That is
                  the honest reading for the 47 manifests captured before the
                  comparison existed -- their text may or may not still match the
                  source, and this archive cannot say which. Reporting COMPLETE
                  there would be asserting the archive agrees with a source
                  nobody re-read.
    """
    checked_at = sync.get("revisions_checked_at")
    compared = _number(sync.get("revisions_compared"))
    # The counts are facts whether or not the date is there: a manifest that
    # records three revisions and no comparison date still records three
    # revisions, and dropping them would be this reader discarding what it read.
    facts = _facts(
        ("revisions_checked_at", checked_at),
        ("revisions_compared", compared),
        ("revision_count", _number(sync.get("revision_count"))),
        ("retraction_count", _number(sync.get("retraction_count"))),
        ("restoration_count", _number(sync.get("restoration_count"))),
    )
    if checked_at is None:
        return {"state": "UNDECIDABLE", "facts": facts,
                "why": ["no `sync.revisions_checked_at`: this capture never "
                        "compared what it recorded against the source, so whether "
                        "an edit or retraction happened after it was taken is not "
                        "recorded here"]}
    if compared is None:
        return {"state": "UNDECIDABLE", "facts": facts,
                "why": ["no readable `sync.revisions_compared`: the manifest says "
                        "a comparison was made and does not say what it looked at, "
                        "so its counts have no denominator"]}
    if compared == 0 and isinstance(written, int) and written > 0:
        return {"state": "INCOMPLETE", "facts": facts,
                "why": [f"`revisions_compared` is 0 while this capture holds "
                        f"{written} messages: the comparison ran and reached none "
                        f"of them, so its zero counts are not a measurement of the "
                        f"source"]}
    if compared == 0 and written is None:
        # 0 compared and nothing that says how many should have been reached.
        return {"state": "UNDECIDABLE", "facts": facts,
                "why": ["`revisions_compared` is 0 and this manifest carries no "
                        "readable `msg_count`, so an empty comparison cannot be "
                        "told from a comparison of an empty capture"]}
    return {"state": "COMPLETE", "facts": facts, "why": []}


def _chat_components(document: dict) -> dict:
    """`messages`, `attachments` and `links`, each decided from its own facts."""
    sync = _require_mapping(document.get("sync"), "`sync`")
    components: dict = {}

    written = _number(sync.get("msg_count"))
    independent = _number(sync.get("remote_unique_message_count"))
    paged = sync.get("pagination_complete")
    facts = _facts(
        ("msg_count", written),
        ("remote_unique_message_count", independent),
        ("pagination_complete", paged),
        ("fetched_page_count", _number(sync.get("fetched_page_count"))),
        ("earliest_create_time", sync.get("earliest_create_time")),
        ("latest_create_time", sync.get("latest_create_time")),
        ("last_message_id", sync.get("last_message_id")),
    )
    # Two failure modes, tracked apart and only then ordered: a recorded
    # shortfall is INCOMPLETE and beats an absence, because the collector looked
    # and said so. A reader that reported UNDECIDABLE there would be saying "we
    # cannot tell" about a manifest that plainly tells.
    why: list[str] = []
    incomplete = undecidable = False
    if paged is False:
        why.append("the collector recorded `pagination_complete: false`: the page "
                   "loop stopped before the source said it was done")
        incomplete = True
    elif paged is None:
        why.append("no `pagination_complete`: whether the page loop reached the end "
                   "was never recorded")
        undecidable = True
    if written is not None and independent is not None and written != independent:
        why.append(f"{written} messages were written and the source's own count is "
                   f"{independent}")
        incomplete = True
    if written is None:
        # Distinct from a disagreement. A source count with no readable local
        # count beside it says how many messages exist and nothing about how
        # many landed, and calling that COMPLETE because the two could not be
        # compared is the collapse this component exists to avoid.
        why.append("no readable `msg_count`: the manifest does not carry the "
                   "number of messages that were written as an integer, so what "
                   "this capture materialized cannot be stated")
        undecidable = True
    if independent is None:
        why.append("no `remote_unique_message_count`: this manifest carries its "
                   "own count and nothing independent to check it against, so "
                   "even an agreeing pair could not have been assembled here")
        undecidable = True
    state = ("INCOMPLETE" if incomplete else
             "UNDECIDABLE" if undecidable else "COMPLETE")
    components["messages"] = {"state": state, "facts": facts, "why": why}

    components["revisions"] = _revisions_component(sync, written)

    if "files" in document:
        files = _require_mapping(document.get("files"), "`files`")
        backfilled = files.get("backfilled")
        backfilled = backfilled if isinstance(backfilled, list) else None
        components["attachments"] = {
            "state": "COMPLETE" if backfilled is not None else "UNDECIDABLE",
            "facts": _facts(("backfilled", None if backfilled is None
                             else len(backfilled))),
            "why": [] if backfilled is not None else
                   ["no `files.backfilled` list: which attachments were written "
                    "was not recorded as a list"],
        }
    if "links" in document:
        links = _require_mapping(document.get("links"), "`links`")
        count = _number(links.get("count"))
        expanded = links.get("expanded")
        expanded = expanded if isinstance(expanded, list) else None
        why = []
        if count is None or expanded is None:
            why.append("the manifest does not carry both `links.count` and a "
                       "`links.expanded` list, so declared and written cannot be "
                       "compared")
            state = "UNDECIDABLE"
        elif count != len(expanded):
            why.append(f"{count} expansions were declared and {len(expanded)} are "
                       f"listed")
            state = "INCOMPLETE"
        else:
            state = "COMPLETE"
        components["links"] = {
            "state": state,
            "facts": _facts(("count", count), ("expanded", None if expanded is None
                                               else len(expanded))),
            "why": why,
        }
    return components


def _identity(shape: str, document: dict, chat: dict) -> dict:
    """Who this capture is of, in that shape's own terms.

    A key is emitted only for the shape that HAS the thing. Until this, every
    shape got a `chat_id`, and on every document manifest in the records tree it
    is `None` -- 20 of 20, because a document manifest carries no `chat` block
    at all (`shape_of` reads it as `url` + `content_sha256`). A key present and
    empty is not an absent key: it says the question was asked and answered,
    when the shape has no such field. The cost is not tidiness. It left every
    document in the tree keyed by nothing, so the two captures of ONE document
    that the tree really holds, under two different directories, are two objects
    here and the record cannot say they are one -- while the twenty documents
    that share a title with nobody are equally indistinguishable.

    The address is not smuggled into `chat_id` for the reason `web_capture`
    already gives: a reader asking one question must not be handed another's
    answer. This is the reader that had nothing, not a third convention --
    `source_policy.identity_of` keys a document capture by `document["url"]`,
    `material_index._identity_of` reads `document.get("url")`, and
    `watch_coverage` reports the manifest's `url` on every link row. All three
    read the same manifests this module reads.
    """
    if shape == "chat_capture":
        # `title` is kept as the fallback it always was, not because a chat
        # manifest here has one -- measured, 0 of the tree's 47 do -- but
        # because dropping it would change a NAME on some other tree to make a
        # point about KEYS, which is not the change this function is.
        return {"chat_id": chat.get("chat_id"),
                "name": chat.get("name") or document.get("title")}
    if shape == "web_capture":
        web_source = _require_mapping(document.get("source"), "`source`")
        observed = _require_mapping(document.get("identity_observed"),
                                    "`identity_observed`")
        return {"url": web_source.get("requested_url"),
                "name": observed.get("title")}
    return {"url": document.get("url"), "name": document.get("title")}


def _document_components(document: dict) -> dict:
    """`body` and `media`, each decided from its own facts."""
    components: dict = {}
    digest = document.get("content_sha256")
    digest = str(digest) if isinstance(digest, str) and digest else None
    why: list[str] = []
    if digest is None:
        why.append("no `content_sha256`: what body landed was not recorded")
        state = "UNDECIDABLE"
    elif len(digest) < FULL_DIGEST_CHARS:
        why.append(f"`content_sha256` is {len(digest)} characters, short of the "
                   f"{FULL_DIGEST_CHARS} a full digest has: this names a body, it "
                   f"does not let anyone re-check one")
        state = "INCOMPLETE"
    else:
        state = "COMPLETE"
    components["body"] = {
        "state": state,
        "facts": _facts(("content_sha256", digest),
                        ("content_sha256_chars", None if digest is None
                         else len(digest)),
                        ("fetched_at", document.get("fetched_at"))),
        "why": why,
    }
    media = document.get("media")
    media = media if isinstance(media, list) else None
    declared = _number(document.get("media_count"))
    why = []
    if declared is None or media is None:
        why.append("the manifest does not carry both `media_count` and a `media` "
                   "list, so declared and written cannot be compared")
        state = "UNDECIDABLE"
    elif declared != len(media):
        why.append(f"{declared} media entries were declared and {len(media)} are "
                   f"listed")
        state = "INCOMPLETE"
    else:
        state = "COMPLETE"
    components["media"] = {
        "state": state,
        "facts": _facts(("media_count", declared),
                        ("media_listed", None if media is None else len(media))),
        "why": why,
    }
    return components


def _web_components(document: dict) -> dict:
    """`body`, `response` and `materials`, each decided from its own facts.

    Three questions a page capture has and a chat's does not, kept apart
    because they fail apart: the bytes may be kept and the address wrong, the
    address right and the server unhelpful, the page fine and nobody having
    looked at what is on it.

    `materials` is the one whose ABSENCE is the finding. A capture that never
    looked for the page's own materials records `inventoried: false`, and
    reading the `0` beside it as "this page has none" is exactly the
    `0 材料且 parser 未知不得 0/0 = 100%` of `DEEP-ARCHIVE-POLICY.md` §5, one
    level down. `UNDECIDABLE` states that in a value no caller can mistake for
    a pass, and does so without claiming the page is empty.

    What is NOT decided here is whether a `200` is really the article: that is
    `docs/EXTERNAL-REFERENCE-LIBRARY.md` §4's login-shell question, and it is
    answered by a module that reads the manifest's shell markers, not by this
    reader endorsing the collector's own `page_kind` label. A label read as a
    state is the misattribution these components exist to prevent.
    """
    source = _require_mapping(document.get("source"), "`source`")
    fetch = _require_mapping(document.get("fetch"), "`fetch`")
    components: dict = {}

    digest = fetch.get("sha256")
    digest = digest if isinstance(digest, str) and digest else None
    why: list[str] = []
    if digest is None:
        why.append("no `fetch.sha256`: what body came back was not recorded")
        state = "UNDECIDABLE"
    elif len(digest) < FULL_DIGEST_CHARS:
        why.append(f"`fetch.sha256` is {len(digest)} characters, short of the "
                   f"{FULL_DIGEST_CHARS} a full digest has: this names a body, "
                   f"it does not let anyone re-check one")
        state = "INCOMPLETE"
    else:
        state = "COMPLETE"
    components["body"] = {
        "state": state,
        "facts": _facts(("content_sha256", digest),
                        ("content_sha256_chars", None if digest is None
                         else len(digest)),
                        ("fetched_at", fetch.get("at"))),
        "why": why,
    }

    status = fetch.get("http_status")
    status = _number(status)
    redirected = source.get("redirected")
    redirected = redirected if isinstance(redirected, bool) else None
    final_url = source.get("final_url")
    final_url = final_url if isinstance(final_url, str) and final_url else None
    why = []
    if status is None:
        why.append("no `fetch.http_status`: whether the server answered is not "
                   "recorded, and a body with no status beside it cannot be "
                   "told from one saved by the author")
        state = "UNDECIDABLE"
    elif status != 200:
        why.append(f"the server answered {status}: whatever came back at this "
                   f"address is not the page, and a capture that records the "
                   f"status is not one that got the page")
        state = "INCOMPLETE"
    else:
        state = "COMPLETE"
    if redirected:
        why.append(f"the request for `{source.get('requested_url')}` ended at "
                   f"`{final_url}`: the bytes here are the ones served there, "
                   f"and a declaration naming the first address is naming an "
                   f"address this capture did not read from")
    components["response"] = {
        "state": state,
        "facts": _facts(("http_status", status), ("final_url", final_url),
                        ("redirected", redirected)),
        "why": why,
    }

    materials = _require_mapping(document.get("materials"), "`materials`")
    inventoried = materials.get("inventoried")
    inventoried = inventoried if isinstance(inventoried, bool) else None
    count = _number(materials.get("count"))
    why = []
    if inventoried is None:
        why.append("the manifest does not record whether the page's own "
                   "materials were looked for, so neither of its two answers "
                   "is on file")
        state = "UNDECIDABLE"
    elif inventoried:
        state = "COMPLETE"
    else:
        why.append(f"`materials.inventoried` is false: the {count} beside it is "
                   f"a count of what was NOT DONE, and reading it as 'this page "
                   f"holds nothing' is the 0/0 the policy forbids")
        state = "UNDECIDABLE"
    components["materials"] = {
        "state": state,
        "facts": _facts(("materials_inventoried", inventoried),
                        ("materials_count", count)),
        "why": why,
    }
    return components


# The prose fields that do assert coverage, reported verbatim. `note` is not
# here on purpose: it is the collector's narrative, and a narrative projected
# into a field reads as a state.
UNSTRUCTURED = {
    "files_pending": "files.pending",
    "links_status": "links.status",
    "links_note": "links.note",
}


def _unstructured(document: dict) -> dict:
    """The collector's own sentences about coverage, kept as sentences."""
    out: dict = {}
    for key, source in UNSTRUCTURED.items():
        section, _, field = source.partition(".")
        holder = document.get(section)
        if isinstance(holder, dict) and holder.get(field) is not None:
            out[key] = {"source": source, "text": holder[field],
                        "note": "this is the collector's prose, reported as written; "
                                "no reader here parses it into a state"}
    return out


def read_manifest(path: Path) -> dict:
    """One manifest as a capture record. A file that cannot be read is a record too.

    A single unreadable manifest must not abort the walk: the denominator is the
    point of this module, and a reader that returned 65 objects where 66 exist
    would understate coverage by exactly the amount it failed to look at.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"shape": "UNREADABLE", "why": f"cannot read {path}: {exc}"}
    if not isinstance(document, dict):
        return {"shape": "UNREADABLE",
                "why": f"{path} holds {type(document).__name__}, not a manifest object"}
    shape = shape_of(document)
    if shape == "UNKNOWN_SHAPE":
        return {"shape": "UNKNOWN_SHAPE",
                "why": f"the manifest carries neither `chat`+`sync`, nor "
                       f"`url`+`content_sha256`, nor `source`+`fetch`: "
                       f"{sorted(document)[:8]}…"}
    chat = _require_mapping(document.get("chat"), "`chat`")
    declared, undeclared = _capture_half(shape, document)
    # `last_verified_at` sits at the manifest's top level on a document capture
    # and inside `sync` on a chat capture. Both are read; neither is inferred
    # from the other. A web capture written by the fetch that first saw it
    # carries neither and is reported as `NOT_RECORDED` rather than given one:
    # a fetch that never re-checked anything has nothing to record here. A web
    # capture that HAS been re-checked carries the top-level key like any other
    # manifest — as of 2026-09-25 `information/raw/web/verify_web.py` writes it,
    # so the sentence above is about a manifest nobody has revisited rather
    # than about the shape. The key is not reserved to one shape and this
    # branch never asked it to be: it reads the manifest, not a family.
    verified_at = document.get("last_verified_at")
    if shape == "chat_capture":
        verified_at = verified_at or \
            _require_mapping(document.get("sync"), "`sync`").get("last_verified_at")
    if shape == "chat_capture":
        components = _chat_components(document)
    elif shape == "web_capture":
        components = _web_components(document)
    else:
        components = _document_components(document)
    identity = _identity(shape, document, chat)
    return {
        "shape": shape,
        "identity": identity,
        "capture": no_unsourced(declared, CAPTURE_PRODUCED, "capture"),
        "capture_undeclared": undeclared,
        "coverage": {
            "components": components,
            "verified": {
                "state": "RECORDED" if verified_at else "NOT_RECORDED",
                "at": verified_at,
                "why": "" if verified_at else
                       "no `last_verified_at`: nothing on this manifest records an "
                       "independent re-check after the capture, so the counts here "
                       "are the collector's own account of its own work",
            },
            "unstructured": _unstructured(document),
        },
        "prev_content_sha256": document.get("prev_content_sha256"),
    }


def read_root(records_root: Path) -> dict:
    """Every manifest under `<records_root>/information/raw`, read as records."""
    root = raw_root(records_root)
    if not root.is_dir():
        raise Refused(
            f"{root} is not a directory: there is no capture tree to read. An "
            f"absent root is not an empty one, and reporting zero objects here "
            f"would say the captures achieved nothing when nothing was looked at")
    objects = []
    for path in walk_manifests(root):
        try:
            record = read_manifest(path)
        except Malformed as exc:
            # `Malformed` only — a `Refused` from this module's own guards is a
            # defect here, and reporting it as an unreadable FILE would blame
            # the manifest for the reader.
            record = {"shape": "UNREADABLE", "why": str(exc)}
        record["path"] = str(path.relative_to(root))
        record["role"] = role_of(path, root)
        if record["shape"] == "UNKNOWN_SHAPE":
            # Stated rather than resolved: the layout says one thing and the
            # keys say nothing, and picking the layout would be this reader
            # deciding a family for a file whose collector did not declare one.
            record["shape_role_disagreement"] = (
                f"the path says role `{record['role']}` and the keys declare no "
                f"shape this reader knows")
        objects.append(record)
    return {"schema": SCHEMA, "root": str(root), "objects": objects,
            "summary": summarize(objects)}


def summarize(objects: list[dict]) -> dict:
    """Counts per component, and the denominator they are over.

    Per component and not per object, because an object-level count would have to
    collapse four independent questions into one number — which is the collapse
    `docs/CC-EXECUTION-PLAN.md` P1 asks this model not to make.
    """
    by_shape: dict = {}
    by_role: dict = {}
    components: dict = {}
    undeclared: dict = {}
    verified = {"RECORDED": 0, "NOT_RECORDED": 0}
    for record in objects:
        by_shape[record["shape"]] = by_shape.get(record["shape"], 0) + 1
        by_role[record["role"]] = by_role.get(record["role"], 0) + 1
        for entry in record.get("capture_undeclared") or []:
            undeclared[entry["field"]] = undeclared.get(entry["field"], 0) + 1
        coverage = record.get("coverage")
        if coverage is None:
            continue
        verified[coverage["verified"]["state"]] += 1
        for name, component in coverage["components"].items():
            bucket = components.setdefault(name, {})
            bucket[component["state"]] = bucket.get(component["state"], 0) + 1
    return {
        "denominator": len(objects),
        "by_shape": by_shape,
        "by_role": by_role,
        "components": components,
        "capture_undeclared": undeclared,
        "verified": verified,
    }
