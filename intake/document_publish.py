"""WHY THIS MODULE EXISTS

`intake/managed_documents.py` answers whether a document's maintenance contract
is a contract we can act on. This one acts on it — once, scoped, and with each
consequence recorded on its own.

The whole reason it is not a shell script around the document API is that the
interesting part of publishing is not the call. It is that the call has five
consequences (正文 / 媒体 / 索引 / Git / 送达), that they fail independently,
and that a failure in one must not turn into a blind rebuild of the others. A
body that landed and media that did not is a state that can only be repaired by
knowing which half landed — so the record here is per-effect, and the module
refuses to collapse the five into one verdict.

WHAT IT REFUSES TO DO

- **It does not fetch or write anything itself.** The two I/O verbs — `fetch`
  (read) and the per-effect writers — are supplied by the caller. What lives
  here is the discipline, not the transport, so the discipline can be read and
  tested without a network.
- **It has no verb for widening an audience.** A request that names an audience
  is refused by name rather than ignored: 权限不扩大 is a property of this path
  (there is nothing here that could widen one), not a policy someone remembers
  to follow. What it does check is the audience fingerprint before and after a
  write, because a body write that quietly changed the audience is the failure
  a permission check placed *before* the write would never see.
- **It does not round-trip unknown content through Markdown.** A section whose
  current blocks include anything outside a fixed list of text-carrying types
  is refused, not serialized and re-parsed. A rich table or an image that
  survives a Markdown round-trip today is a coincidence, and 图片/富表 losing
  their identity on the way back is not a failure anyone would see in the diff.
- **It does not retry an effect whose outcome is unknown.** A receipt written
  before an attempt and not closed after it means the attempt was interrupted.
  The next run reads the live state back and decides, because re-issuing a body
  write that may already have landed is how one section becomes two.
- **It does not claim a local lock means global safety.** Nothing here locks
  anything. The concurrency control is the service's own `revision_id`
  optimistic check, passed through on every body write, plus a refusal to write
  when the base we read is not the base we were given.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import tempfile
from pathlib import Path

SCHEMA = "serveros/document-publish.v1"
REQUEST_SCHEMA = "serveros/document-publish-request.v1"

#: The five consequences, kept separate all the way to the report. Order is the
#: order they are attempted in, and an earlier one not reaching `OK` or
#: `ALREADY_EFFECTED` is what makes a later one `NOT_ATTEMPTED`.
EFFECTS = ("body", "media", "index", "git", "delivery")

#: What can be said about one effect. Each of these is a different fact and the
#: differences are load-bearing:
#:
#: - `OK`                 did it, and read it back
#: - `ALREADY_EFFECTED`   the live state already is what the intent asked for —
#:                        the second write would be the duplicate
#: - `REFUSED`            did not try: a condition that has to hold did not
#: - `FAILED`             tried, and it did not work; why is recorded
#: - `NOT_ATTEMPTED`      applies here, but an earlier effect stopped the run
#: - `NOT_APPLICABLE`     does not apply to this object or this run at all
#: - `UNKNOWN_OUTCOME`    attempted and interrupted; the state is not known and
#:                        must be read back before anything is re-issued
EFFECT_STATES = ("OK", "ALREADY_EFFECTED", "REFUSED", "FAILED",
                 "NOT_ATTEMPTED", "NOT_APPLICABLE", "UNKNOWN_OUTCOME")

#: A state from which the run is allowed to carry on to the next effect. Note
#: that `ALREADY_EFFECTED` is in here and `UNKNOWN_OUTCOME` is not.
SETTLED = ("OK", "ALREADY_EFFECTED")

#: Block types whose text survives a round trip through this module. A block
#: type outside this list inside the patch range is refused: the range is
#: replaced wholesale, and "wholesale" over a type nobody modelled is loss.
ROUND_TRIPPABLE = ("page", "text", "heading1", "heading2", "heading3",
                   "heading4", "heading5", "heading6", "heading7", "heading8",
                   "heading9", "bullet", "ordered", "code", "quote", "todo",
                   "divider")

#: The only content format this path will send. Markdown is refused rather than
#: accepted with a warning, because a Markdown round trip is exactly the
#: mechanism by which an unknown block stops being itself.
CONTENT_FORMATS = ("xml",)

#: Heading block types, for finding where a section starts and ends.
HEADING_TYPES = ("heading1", "heading2", "heading3", "heading4", "heading5",
                 "heading6", "heading7", "heading8", "heading9")

#: Carried by every planned run. A plan has not read the document, so the gate
#: is asked there without the original text — and two of the ability's three
#: protections are counts against that text, which an empty one satisfies
#: vacuously. A plan that passed because nobody looked must not read as a plan
#: that passed because the edit is safe.
PLAN_GATE_NOTE = ("闸门在计划阶段是拿空原文问的：与原文比计数的保护（完成标记消失、"
                  "不确定被抹掉）在这种情况下**空过**。所以计划通过不等于写入会通过 ——"
                  " `--apply` 会在读到文档之后、拿真原文再问一次")


class Refused(Exception):
    """The request or the live state does not support a write. Nothing written."""


class Malformed(Exception):
    """The inputs are not the shape this module reads."""


# ────────────────────────────────────────────────────────────────────────────
# receipts — one per (document, effect), written before and closed after
# ────────────────────────────────────────────────────────────────────────────

def receipt_path(receipts_dir: Path, document: str, effect: str) -> Path:
    """Where one effect's receipt lives. Per effect, never one per document.

    A single receipt for the whole publish is what makes "body landed, media did
    not" unrepresentable, and that is the state the acceptance clause is about.
    """
    return Path(receipts_dir) / document / f"{effect}.json"


def read_receipt(receipts_dir: Path, document: str, effect: str) -> dict | None:
    """The last thing we recorded about this effect, or `None` for never.

    `None` means we have never attempted it — which is not the same as a receipt
    saying it failed, and the two lead to different next moves.
    """
    path = receipt_path(receipts_dir, document, effect)
    if not path.is_file():
        return None
    try:
        document_json = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # An unreadable receipt is the interrupted case, not a missing one: we
        # know an attempt existed and we cannot say what came of it.
        return {"effect": effect, "state": "UNKNOWN_OUTCOME",
                "why": f"receipt at {path} could not be read; the last attempt's "
                       f"outcome is not known from here"}
    if not isinstance(document_json, dict):
        return {"effect": effect, "state": "UNKNOWN_OUTCOME",
                "why": f"receipt at {path} is not a mapping"}
    return document_json


def write_receipt(receipts_dir: Path, document: str, effect: str,
                  receipt: dict) -> None:
    """Atomic write, so a receipt is never half-recorded."""
    path = receipt_path(receipts_dir, document, effect)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2)
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with open(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def intent_key(effect: str, request: dict) -> str:
    """What "the same intent" means for one effect.

    Content-addressed rather than time-addressed: running the same request twice
    has to be recognisable as the same request, or idempotency degenerates into
    "we have not run this recently".

    `base_revision` is deliberately **not** part of it. The base is the one field
    that changes between two runs of the same generated intent — any other edit
    to the document moves it, including this path's own previous write — so
    folding it in makes every re-run a new intent, and a daily routine that
    re-runs would rewrite its own section, bump the revision and mint new block
    ids for text that did not change. That was measured: the second real run of
    one published section wrote again with byte-identical content. The stale-base
    protection is unaffected, because that check is against the live document and
    not against the key.
    """
    material = {"effect": effect, "document": request.get("document"),
                "section": request.get("section")}
    if effect == "body":
        material["content_sha256"] = _sha(request.get("content") or "")
    elif effect == "media":
        material["media"] = request.get("media")
    else:
        material["payload"] = request.get(effect)
    return _sha(json.dumps(material, ensure_ascii=False, sort_keys=True))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ────────────────────────────────────────────────────────────────────────────
# the request
# ────────────────────────────────────────────────────────────────────────────

def read_request(path: Path) -> dict:
    """One publish request, or a refusal that says which file was opened."""
    path = Path(path)
    if not path.is_file():
        raise Refused(f"request not found: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise Refused(f"request could not be read: {exc}") from exc
    except ValueError as exc:
        raise Refused(f"request is not JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise Malformed("a publish request must be a mapping")
    if document.get("schema") != REQUEST_SCHEMA:
        raise Malformed(f"request schema is {document.get('schema')!r}, "
                        f"not {REQUEST_SCHEMA!r}")
    return document


def check_request(request: dict) -> list[str]:
    """The refusals that do not need a document, in the order they matter.

    Returned rather than raised so the report can carry all of them: a caller
    fixing one refusal at a time because the module only mentions the first is a
    caller who will come back four times.
    """
    refusals: list[str] = []

    if request.get("audience") is not None:
        # Not "we will ignore it" — a request that asks for an audience change
        # is asking for the one thing this path cannot do, and silently dropping
        # the field would leave the caller believing it happened.
        refusals.append(
            "audience: this path has no verb for changing who can read a "
            "document, so a request naming an audience is refused rather than "
            "read as a no-op")

    fmt = request.get("doc_format", "xml")
    if fmt not in CONTENT_FORMATS:
        refusals.append(
            f"doc_format {fmt!r} is not one of {list(CONTENT_FORMATS)} — "
            f"Markdown is refused because a round trip through it is how an "
            f"unknown block or a rich table stops being itself")

    if not request.get("document"):
        refusals.append("document: no document named")
    if not request.get("section"):
        refusals.append("section: no section key named; a patch with no named "
                        "section has no scope, and scope is what protects every "
                        "block the patch is not about")
    if not isinstance(request.get("base_revision"), int):
        refusals.append("base_revision: no base revision; without one there is "
                        "nothing to compare the live revision against, and a "
                        "write that cannot notice a moved base is a write over "
                        "somebody else's edit")
    if not request.get("content"):
        refusals.append("content: nothing to write")
    if request.get("change_class") not in ("PATCH", "REFINE", "RESTRUCTURE"):
        refusals.append("change_class: not one of PATCH/REFINE/RESTRUCTURE; the "
                        "class decides which tier of the contract's edit_policy "
                        "governs, so it is not a defaultable field")
    return refusals


# ────────────────────────────────────────────────────────────────────────────
# reading what is there now
# ────────────────────────────────────────────────────────────────────────────

def find_section(blocks: list, heading: str) -> dict:
    """Where one section's **body** is, by its heading text.

    The body runs from just after the heading to the next heading of the same or
    a higher level, and the heading is deliberately **not** in the range this
    returns. That is not tidiness. A section is identified downstream by its
    heading text — `sections[].match.heading` is the key the whole contract
    indexes on — so a patch that replaces the heading has moved the anchor the
    next patch will look for, and the failure shows up one run later as "no
    heading matches". Renaming a heading is therefore not this verb's business;
    a body patch keeps the heading, and the digest of everything outside the
    range is what proves it kept it.

    Both location failures are refusals rather than guesses: a heading that is
    not there has no range, and a heading that appears twice has two, and
    picking the first is how a patch lands on the wrong one of two identically
    named sections.
    """
    hits = [index for index, block in enumerate(blocks)
            if block.get("type") in HEADING_TYPES
            and (block.get("text") or "").strip() == heading.strip()]
    if not hits:
        raise Refused(f"no heading matches {heading!r} in the fetched document; "
                      f"a section that cannot be located cannot be patched")
    if len(hits) > 1:
        raise Refused(f"{len(hits)} headings match {heading!r}; which one is "
                      f"meant is not something this path will guess")
    start = hits[0] + 1
    level = _heading_level(blocks[hits[0]].get("type"))
    end = len(blocks)
    for index in range(start, len(blocks)):
        if blocks[index].get("type") in HEADING_TYPES \
                and _heading_level(blocks[index].get("type")) <= level:
            end = index
            break
    inside = blocks[start:end]
    return {"heading_id": blocks[hits[0]].get("id"),
            "heading_type": blocks[hits[0]].get("type"),
            "start": start, "end": end,
            "blocks": inside,
            "body_ids": [block.get("id") for block in inside]}


def _heading_level(block_type: str | None) -> int:
    return int((block_type or "heading9")[-1])


def untrippable(section: dict) -> list[str]:
    """Block types inside the range that this module will not replace."""
    return [f"{block.get('id')}:{block.get('type')}"
            for block in section["blocks"]
            if block.get("type") not in ROUND_TRIPPABLE]


def outside_digest(blocks: list, section: dict) -> str:
    """A digest of everything the patch is not about.

    Compared before and after. A patch that also changed a block outside its own
    range is the failure mode where "scoped" was an intention rather than a
    check — and it is invisible in a review of the patch itself.
    """
    parts = [f"{block.get('id')}|{block.get('type')}|{block.get('text') or ''}"
             for index, block in enumerate(blocks)
             if not (section["start"] <= index < section["end"])]
    return _sha("\n".join(parts))


def audience_fingerprint(observed) -> str | None:
    """A stable digest of who can read the document, order-insensitively.

    `None` for an audience nobody observed — deliberately not a digest of the
    empty set. "Nobody can read this" and "we did not look" must not compare
    equal, or the permission check passes on the strength of not having been
    performed, which is the exact shape of a check that is worse than none.
    """
    if observed is None:
        return None
    return _sha("\n".join(sorted(str(one) for one in observed)))


# ────────────────────────────────────────────────────────────────────────────
# planning
# ────────────────────────────────────────────────────────────────────────────

def plan(request: dict, contract: dict, *, gate_decision: dict,
         approvals: dict | None = None) -> dict:
    """What would be attempted, effect by effect, and what stops each one.

    Pure: no reads, no writes, no clock. Everything that depends on the live
    document is decided in `apply`, because a plan that silently read the live
    document would be a plan whose refusals could not be reproduced.
    """
    refusals = check_request(request)
    approvals = approvals or {}

    state = contract.get("contract")
    if state != "CHECKED":
        refusals.append(f"contract: {state} — the maintenance contract is not "
                        f"readable as a contract, so there is nothing that says "
                        f"what may be written here"
                        + (f" ({'; '.join(contract.get('reasons') or [])})"
                           if contract.get("reasons") else ""))

    delivery = contract.get("delivery") or {}
    if delivery.get("state") != "CONSISTENT":
        refusals.append(f"delivery: {delivery.get('state')} — {delivery.get('why')}")
    elif delivery.get("form") == "FOLLOW_MIRROR" and not request.get("mirror_ack"):
        refusals.append(
            f"mirror_ack: this object is a mirror of "
            f"{contract.get('copied_from') or '（契约没写 copied_from）'}, so its "
            f"body is a copy — writing here diverges it from its source. If that "
            f"is what is meant, say so; the default is not to find out later "
            f"from a diff between two documents that were supposed to be one")

    policy = contract.get("write_policy") or {}
    tiers = policy.get("tiers") or {}
    change_class = request.get("change_class")
    tier = tiers.get(change_class)
    if tier is None:
        refusals.append(f"write_policy: the contract declares no tier for "
                        f"{change_class}")
    elif tier == "require_approval" or gate_decision.get("decision") == "approval":
        if not approvals.get(change_class):
            refusals.append(
                f"approval: {change_class} lands on the approval tier and this "
                f"request carries no approval for it; a structural rewrite is a "
                f"human judgement about appearance, audience and tone")

    decision = gate_decision.get("decision")
    if decision == "blocked":
        refusals.append(f"gate: {gate_decision.get('reason')}")

    if refusals:
        effects = [{"effect": effect, "state": "REFUSED",
                    "why": "the run did not start: see refusals"}
                   for effect in EFFECTS]
        return {"schema": SCHEMA, "document": request.get("document"),
                "section": request.get("section"), "planned": False,
                "refusals": refusals, "effects": effects}

    return {"schema": SCHEMA, "document": request.get("document"),
            "section": request.get("section"), "planned": True,
            "refusals": [], "notes": [PLAN_GATE_NOTE],
            "effects": [{"effect": effect, "state": "NOT_ATTEMPTED",
                         "why": "planned, not yet attempted"}
                        for effect in EFFECTS]}


# ────────────────────────────────────────────────────────────────────────────
# applying
# ────────────────────────────────────────────────────────────────────────────

def apply(request: dict, contract: dict, *, gate_decision: dict,
          fetch, writers: dict, receipts_dir: Path,
          approvals: dict | None = None, gate=None) -> dict:
    """Carry out a plan, one effect at a time, each with its own receipt.

    `fetch(document, scope) -> {"revision", "blocks", "audience"}` is the read;
    `writers` maps an effect name to the callable that performs it. A missing
    writer is `NOT_APPLICABLE` for that effect, not a silent skip: the report has
    to distinguish "this object has no media" from "this run did not do media".

    `gate(original_text) -> decision` is the ability's own gate, asked **after**
    the live read and with the text the live read found. That ordering is not a
    detail. Two of the ability's three protections are counted comparisons
    against the original text — how many `完成` strikethrough spans disappear,
    how many `不确定` markers were erased — and a gate asked before the read, or
    asked with an empty original, passes both **vacuously**. A protection that
    is satisfied by not having looked is worse than one that is absent, because
    it reports as satisfied.
    """
    outcome = plan(request, contract, gate_decision=gate_decision,
                   approvals=approvals)
    if not outcome["planned"]:
        return outcome

    document = request["document"]
    effect_rows: list[dict] = []

    # ── body ────────────────────────────────────────────────────────────────
    body = _apply_body(request, fetch, writers.get("body"), receipts_dir,
                       gate)
    effect_rows.append(body)

    # ── the rest ────────────────────────────────────────────────────────────
    for effect in EFFECTS[1:]:
        effect_rows.append(_apply_tail(effect, request, writers.get(effect),
                                       receipts_dir, effect_rows))

    outcome = {**outcome, "effects": effect_rows}
    if body.get("state") in SETTLED:
        outcome["settled"] = all(row["state"] in SETTLED for row in effect_rows
                                 if row["state"] != "NOT_APPLICABLE")
    else:
        outcome["settled"] = False
    return outcome


def _apply_body(request: dict, fetch, writer, receipts_dir: Path,
                gate=None) -> dict:
    """The body write: the only effect with a scope, and the only one with a
    concurrency check worth the name."""
    document = request["document"]
    effect = "body"
    key = intent_key(effect, request)
    previous = read_receipt(receipts_dir, document, effect)

    # There is deliberately **no** receipt-based short circuit for the body,
    # although the other effects have one. A receipt says what a previous run
    # believed; the body is the one effect whose result can be read off the
    # document itself, so the document is asked and the receipt is not trusted
    # to answer for it. That is also why the body's re-run is idempotent even
    # when the receipts have been lost, which is the state a fresh install or a
    # moved state directory is in.
    if writer is None:
        return {"effect": effect, "state": "NOT_APPLICABLE",
                "why": "this run supplied no body writer"}

    # The live read. Everything that decides whether to write comes from here.
    try:
        live = fetch(document, request.get("scope"))
    except Exception as exc:                       # the transport's own failure
        row = {"effect": effect, "state": "FAILED",
               "why": f"the document could not be read: {exc}"}
        write_receipt(receipts_dir, document, effect,
                      _receipt(effect, key, "FAILED", row["why"]))
        return row

    revision = live.get("revision")
    try:
        section = find_section(live.get("blocks") or [], request.get("heading")
                               or request.get("section"))
    except Refused as exc:
        row = {"effect": effect, "state": "REFUSED", "why": str(exc)}
        write_receipt(receipts_dir, document, effect,
                      _receipt(effect, key, "REFUSED", row["why"]))
        return row

    # "There is nothing to do" outranks "the base moved". A stale base is only a
    # problem for a *write*, and this is the check that finds there is none to
    # make. It is also the interrupted-attempt rule, stated statelessly: an
    # attempt that was never closed is resolved by looking at the document and
    # seeing whether what it wanted is already there — not by trusting a receipt,
    # and never by re-issuing. Two runs of one intent therefore converge on one
    # write however they are interleaved, and a re-run of a section that already
    # says this writes nothing, bumps no revision and mints no new block ids.
    if section_matches(section, request["content"]):
        interrupted = bool(previous and previous.get("intent") == key
                           and previous.get("state") == "UNKNOWN_OUTCOME")
        why = ("the section already holds exactly this text; a second write "
               "would change no content and would bump the revision")
        if interrupted:
            why += ("（上一轮这一次尝试没有闭合，这次以读回文档为准，不重发）")
        write_receipt(receipts_dir, document, effect,
                      _receipt(effect, key, "ALREADY_EFFECTED", why))
        return {"effect": effect, "state": "ALREADY_EFFECTED", "why": why,
                "previous_interrupted": interrupted,
                "revision": revision}

    if revision != request["base_revision"]:
        row = {"effect": effect, "state": "REFUSED",
               "why": f"revision {request['base_revision']} -> {revision}: the "
                      f"base moved, so any patch would be built on a document "
                      f"that no longer exists. Rebuild the candidate from the "
                      f"new revision; do not write this one"}
        write_receipt(receipts_dir, document, effect,
                      _receipt(effect, key, "REFUSED", row["why"]))
        return row

    if gate is not None:
        # Asked here, with what is actually there. See `apply` for why this is
        # not asked earlier and not asked with an empty original.
        live_text = "\n".join(block.get("text") or "" for block in section["blocks"])
        asked = gate(live_text)
        if asked.get("decision") == "blocked":
            row = {"effect": effect, "state": "REFUSED",
                   "why": f"the gate refused this edit against the text the "
                          f"document actually holds: {asked.get('reason')}",
                   "gate": asked}
            write_receipt(receipts_dir, document, effect,
                          _receipt(effect, key, "REFUSED", row["why"]))
            return row

    blocked = untrippable(section)
    if blocked:
        row = {"effect": effect, "state": "REFUSED",
               "why": f"the section holds block types this path will not "
                      f"replace: {blocked}. Replacing the range would send them "
                      f"through a round trip they do not survive"}
        write_receipt(receipts_dir, document, effect,
                      _receipt(effect, key, "REFUSED", row["why"]))
        return row

    before_outside = outside_digest(live.get("blocks") or [], section)
    before_audience = audience_fingerprint(live.get("audience"))

    write_receipt(receipts_dir, document, effect,
                  _receipt(effect, key, "UNKNOWN_OUTCOME",
                           "attempt in progress or interrupted"))

    try:
        result = writer(document,
                        {"heading_id": section["heading_id"],
                         "body_ids": section["body_ids"]},
                        request["content"], revision)
    except Exception as exc:
        write_receipt(receipts_dir, document, effect,
                      _receipt(effect, key, "UNKNOWN_OUTCOME",
                               f"the write raised: {exc}"))
        return {"effect": effect, "state": "UNKNOWN_OUTCOME",
                "why": f"the write raised: {exc}. Whether it landed is not "
                       f"known from here — the next run reads back first"}

    # Readback. Three things are checked, and they are checked separately
    # because they fail for different reasons.
    #
    # A readback that cannot be performed leaves the outcome unknown, and it
    # must not raise: the write has already happened, and an exception thrown
    # here would report the run as a crash rather than as "written, not
    # verified". The receipt stays UNKNOWN_OUTCOME either way, so the next run
    # reads back instead of rebuilding.
    try:
        after = fetch(document, request.get("scope"))
        after_section = find_section(after.get("blocks") or [],
                                     request.get("heading") or request.get("section"))
    except Exception as exc:
        why = (f"the write was sent, but the document could not be read back to "
               f"confirm it: {exc}")
        write_receipt(receipts_dir, document, effect,
                      _receipt(effect, key, "UNKNOWN_OUTCOME", why))
        return {"effect": effect, "state": "UNKNOWN_OUTCOME", "why": why,
                "result": result}
    problems = []
    if not section_matches(after_section, request["content"]):
        problems.append("the section read back does not match what was written")
    after_outside = outside_digest(after.get("blocks") or [], after_section)
    if after_outside != before_outside:
        problems.append("a block outside the patched range changed")
    after_audience = audience_fingerprint(after.get("audience"))
    audience_checked = before_audience is not None and after_audience is not None
    if audience_checked and after_audience != before_audience:
        problems.append("the audience changed as a result of the write")

    if problems:
        write_receipt(receipts_dir, document, effect,
                      _receipt(effect, key, "FAILED", "; ".join(problems)))
        return {"effect": effect, "state": "FAILED",
                "why": "; ".join(problems),
                "audience_checked": audience_checked,
                "revision": after.get("revision"),
                "result": result}

    why = f"written and read back at revision {after.get('revision')}"
    if not audience_checked:
        # Said out loud rather than left to the reader. A pass that did not check
        # the audience is a pass on the two things it did check.
        why += "；受众未观测，本次没有核对受众有没有变"
    write_receipt(receipts_dir, document, effect,
                  _receipt(effect, key, "OK", why))
    return {"effect": effect, "state": "OK", "why": why,
            "audience_checked": audience_checked,
            "revision": after.get("revision"), "result": result}


def _apply_tail(effect: str, request: dict, writer, receipts_dir: Path,
                so_far: list[dict]) -> dict:
    """One of media/index/git/delivery, with the two absences kept apart."""
    document = request["document"]
    key = intent_key(effect, request)

    declared = request.get(effect)
    if declared == "NONE":
        return {"effect": effect, "state": "NOT_APPLICABLE",
                "why": f"the request declares {effect}=NONE"}
    if writer is None:
        return {"effect": effect, "state": "NOT_APPLICABLE",
                "why": f"this run supplied no {effect} writer"}

    # A writer was supplied and applies. Whether it is reached depends on
    # everything before it, and that dependency is the point of the ordering.
    stopped_by = [row for row in so_far
                  if row["state"] not in SETTLED and row["state"] != "NOT_APPLICABLE"]
    if stopped_by:
        return {"effect": effect, "state": "NOT_ATTEMPTED",
                "why": f"not reached: {stopped_by[0]['effect']} is "
                       f"{stopped_by[0]['state']}"}

    previous = read_receipt(receipts_dir, document, effect)
    if previous and previous.get("state") in SETTLED \
            and previous.get("intent") == key:
        return {"effect": effect, "state": "ALREADY_EFFECTED",
                "why": "a previous run recorded this same intent as landed"}

    write_receipt(receipts_dir, document, effect,
                  _receipt(effect, key, "UNKNOWN_OUTCOME",
                           "attempt in progress or interrupted"))
    try:
        result = writer(document, request)
    except Exception as exc:
        write_receipt(receipts_dir, document, effect,
                      _receipt(effect, key, "FAILED", str(exc)))
        return {"effect": effect, "state": "FAILED", "why": str(exc)}
    write_receipt(receipts_dir, document, effect,
                  _receipt(effect, key, "OK", "reported landed"))
    return {"effect": effect, "state": "OK", "why": "performed and reported",
            "result": result}


def section_matches(section: dict, content: str) -> bool:
    """Whether the section as it now stands carries the written text.

    Compared on the visible text rather than on the markup: the service is
    allowed to normalise how it stores what it was given, and comparing markup
    would turn its normalisation into our failure. What must not be normalised
    away is the text.
    """
    written = _visible_text(content)
    present = "\n".join((block.get("text") or "") for block in section["blocks"])
    for line in written.splitlines():
        if line.strip() and line.strip() not in present:
            return False
    return True


def _visible_text(content: str) -> str:
    """The text of an XML fragment, one line per element, tags removed.

    One line per element and not one run-on string. Where one paragraph ends and
    the next begins is part of what the content says, and a reader that drops it
    turns every multi-paragraph write into a readback that disagrees with the
    document — while the document is exactly right. That happened, on the first
    real write this path ever made, and it reported the document as wrong.
    """
    out: list[str] = []
    depth = 0

    def break_line() -> None:
        if out and out[-1] != "\n":
            out.append("\n")

    for char in content:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
            # Exactly one site, at the close of a top-level element. Two sites —
            # one here and one at the open — would be redundant, and a rule whose
            # removal needs two edits to observe is a rule no single test can
            # hold down.
            if depth == 0:
                break_line()
        elif depth == 0:
            out.append(char)
    text = "".join(out)
    for entity, plain in (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"),
                          ("&quot;", '"'), ("&apos;", "'")):
        text = text.replace(entity, plain)
    return text


def _receipt(effect: str, intent: str, state: str, why: str) -> dict:
    return {"effect": effect, "intent": intent, "state": state, "why": why,
            "at": datetime.datetime.now(datetime.timezone.utc)
                  .isoformat(timespec="seconds")}


# ────────────────────────────────────────────────────────────────────────────
# rendering
# ────────────────────────────────────────────────────────────────────────────

def render(report: dict) -> list[str]:
    """The report as lines. Every effect gets a line even when it did nothing."""
    lines = [f"document    {report.get('document')}",
             f"section     {report.get('section')}"]
    if not report.get("planned"):
        lines.append("")
        lines.append("✗ 没有开始：")
        lines.extend(f"  · {reason}" for reason in report.get("refusals") or [])
        return lines

    attempted = [row for row in report.get("effects") or []
                 if row["state"] != "NOT_ATTEMPTED"]
    verdict = ("全部落定" if report.get("settled") else
               "计划，未执行" if not attempted else "未落定")
    lines.append(f"结局        {verdict}")
    for note in report.get("notes") or []:
        lines.append(f"注意        {note}")
    lines.append("")
    for row in report.get("effects") or []:
        mark = {"OK": "✓", "ALREADY_EFFECTED": "=", "FAILED": "✗",
                "REFUSED": "✗", "NOT_ATTEMPTED": "·", "NOT_APPLICABLE": "—",
                "UNKNOWN_OUTCOME": "?"}.get(row["state"], "?")
        lines.append(f"{mark} {row['effect']:<9} {row['state']:<17} "
                     f"{row.get('why')}")
    return lines
