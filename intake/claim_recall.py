#!/usr/bin/env python3
"""A2 — what may still be recalled, decided from the durable record.

    serverosctl work claim-recall --records-root /abs/records

Three documents give this module its job and its vocabulary, and none of them
had an implementation:

  * `docs/INFORMATION-DOCUMENT-LIFECYCLE.md` §9 — "A2 过滤撤回/过期". §7 says a
    source correction, a withdrawal or a permission change "locates the affected
    Claims … and marks them precisely stale / conflict / 不可召回", and adds two
    constraints this module obeys: 局部已提交的变化**先保留 receipt**再恢复依赖传播
    (an already-committed local change keeps its receipt while propagation
    resumes), and 不能全库重写来"同步" (do not rewrite the store to sync).
  * `docs/daily-work-cycle.md` — 撤回/失效**先在本地召回过滤生效**，远端 Memory
    更新晚到不复活旧结论. Withdrawal takes effect in local recall FIRST, and a
    late remote update must not revive the old conclusion. That is why nothing
    here asks a provider anything.
  * `docs/DEEP-ARCHIVE-POLICY.md` §7 — 旧 COMPLETE 仅旧版，下游失效可核: the old
    completion covers the old revision only, and what went stale downstream has
    to be checkable.

What this module reads, and what it refuses to invent
----------------------------------------------------
The durable record of what was written into the understanding layer is the
receipt `understanding_semantic` leaves behind — one per committed reading, at
`<understanding root>/.serveros/receipt-<key slug>.json`. There is no other
record and this module does not create one. From each receipt it takes the
proposal key (which carries the SPAN and the source's content identity at the
moment of the read), which claims went into which card, what was replaced, and
what a replacement failed to reach.

One thing the receipt does not state outright is which write PUT a line in a
card: `written[i].claims` is what the card carries afterwards, and a card
carries every commit that ever landed in it. The write that put a given line
there is that set minus what the card carried before it, over the sequence of
writes in time order — `carried_forward` below. Without that subtraction a
claim read from one span is given the span of whatever commit last touched its
card, and a neighbouring range read later would decide whether the older claim
is still current.

The other half is the source itself. Whether a claim is still current is not a
question about the receipt — it is a question about the text the claim was read
from, and that is answered by taking the span's digest again from the capture's
own messages file with `understanding_delta.range_content_digest`, the one
function in this tree that computes it. Two derivations of "the digest of this
span" would drift, and the one that drifts is the one that decides whether a
person's earlier yes still holds.

The rule that decides recallability is not written here either. It is
`daily_cycle.invalidate_claims`, which has been in this tree since before this
module, tested, and with no caller. It takes claim rows and the current
revisions and answers `recallable`. What it never had was a producer for its own
inputs: `source_revisions`, `verification` and `withdrawn` were fields somebody
was supposed to supply and nobody did. This module supplies them, each from
something observed, and refuses to answer at all if the rule's verdict and the
label it computed disagree — an answer a consumer acts on while the rule says
otherwise is worse than no answer.

Why the key is the span and not the file
----------------------------------------
`invalidate_claims` compares `source_revisions[key]` row by row, so the key
decides which revision a claim is compared against. Keyed by the capture
object, two claims read from one chat at two different spans would share one
digest, and the later span's drift would mark the earlier claim stale while the
earlier span's stability would keep a drifted claim current. The span is the
unit `understanding_delta` measures and the unit the receipts are keyed on, so
it is the unit here.

Why withdrawal is a local verdict and not a provider call
--------------------------------------------------------
`understanding_semantic.eligible` states it: a retraction takes effect locally
and immediately, "and not when a remote memory provider acknowledges it — the
contract's §6 says the remote can be down and the withdrawn fact still must not
come back". So no provider is consulted, and its absence is reported as an axis
rather than being allowed to change an answer. The same discipline covers the
axes this module cannot observe at all: they are named in `unobserved`, because
a filter that silently checks nothing is indistinguishable from one that
checked and found nothing.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCHEMA = "serveros/claim-recall.v1"

# --------------------------------------------------------------------------
# the verdicts
# --------------------------------------------------------------------------

#: Nothing was found against the claim: it is where it was put, the text it was
#: read from still digests to what it digested to, and no durable record says it
#: was replaced.
CURRENT = "CURRENT"
#: The span this claim was read from no longer digests to what it did. A
#: correction, an edit, a deletion, or a source that was re-published. The claim
#: is not wrong, it is about a text that is no longer there.
STALE = "STALE"
#: A later commit named this claim as replaced. `removal_landed` says whether the
#: line actually left its card: when it did not, the replaced claim is still in a
#: card reading as current, and that is the state
#: `understanding_semantic._superseded_unmet` exists to report rather than hide.
SUPERSEDED = "SUPERSEDED"
#: The card the receipt says carries this claim's line does not carry it, and
#: nothing in the record accounts for the removal. A hand edit, a write whose
#: receipt was lost, or a card that was replaced wholesale.
MISSING = "MISSING"
#: The span cannot be identified now — the capture's messages file is gone, or
#: no record in it carries a position in the range. Nothing was compared, so
#: this is deliberately NOT `STALE`: calling it stale would be a statement about
#: content nobody read. It is not recallable either, because nothing backs it.
UNRESOLVED = "UNRESOLVED"
#: The durable record disagrees with itself about this id: two receipts wrote a
#: claim under it from two different spans, or the same id is carried by two
#: cards. An id is what a supersede and a retraction name, so an id naming two
#: claims makes every reference through it ambiguous, and no rule can resolve
#: that — a person has to look at the record.
CONFLICT = "CONFLICT"

VERDICTS = (CURRENT, STALE, SUPERSEDED, MISSING, UNRESOLVED, CONFLICT)

#: The precedence, stated rather than emergent. `CONFLICT` comes first because it
#: is a statement about the record itself: nothing read out of a record that
#: disagrees with itself should be trusted before somebody has looked.
#: `SUPERSEDED` before `MISSING` because a recorded replacement EXPLAINS an
#: absence — without that order every successfully superseded claim would come
#: back `MISSING`. Then the two source states, `STALE` before `UNRESOLVED`
#: because a comparison that happened outranks one that could not. `CURRENT` is
#: the absence of all five and is last.
PRECEDENCE = (CONFLICT, SUPERSEDED, MISSING, STALE, UNRESOLVED, CURRENT)

#: The doc's own three labels, per verdict. Present so that a reader comparing
#: this output with `docs/INFORMATION-DOCUMENT-LIFECYCLE.md` §7 can see that
#: `SUPERSEDED`, `MISSING` and `UNRESOLVED` are all 不可召回, split because the
#: repairs differ — the same reason `memory/layering.py` names its two drop
#: reasons instead of counting them.
MARK = {
    CURRENT: "可召回",
    STALE: "stale",
    SUPERSEDED: "不可召回",
    MISSING: "不可召回",
    UNRESOLVED: "不可召回",
    CONFLICT: "conflict",
}

#: The verdicts a consumer may act on. One, and it is the one the rule agrees
#: with; everything else is something for a person or a re-read.
RECALLABLE_VERDICTS = (CURRENT,)

#: `verification` values for the rows handed to `invalidate_claims`, and where
#: each comes from. Not a decoration of that rule's field: the rule branches on
#: `verification == "EVIDENCED"`, so a constant here would make that branch dead
#: and would make the module look like it had checked something.
EVIDENCED = "EVIDENCED"
NOT_EVIDENCED = "NOT_EVIDENCED"

#: The axes this filter cannot see, said out loud. Each is a real question a
#: caller may believe was answered by a verdict about recallability.
UNOBSERVED = {
    "permission": (
        "there is no source-permission store in this fabric — `docs/memory-"
        "fabric.md` refuses a second place for that answer to live — so a "
        "revoked permission cannot be, and is not, a reason this filter drops "
        "a claim. It is named here so that `CURRENT` is not read as 'the "
        "source is still allowed to be recalled from'"),
    "provider": (
        "no memory provider is consulted. The contract says the remote can be "
        "down and a withdrawn fact must not come back, so an answer that "
        "depended on one would be a different answer on a different day"),
    "line_text": (
        "a receipt records which claim ids a card carries and the card's two "
        "digests, never the line's text, so this filter cannot tell a line that "
        "was edited in place from one that was not. `card_changed_since` says "
        "whether something in the card changed; it does not say that this claim "
        "is what changed"),
    "retraction": (
        "a RETRACTION is not visible to this filter, and unlike the other axes "
        "here the RULE for it exists and is tested — "
        "`understanding_semantic.eligible(claimed, retractions)` states that a "
        "withdrawal takes effect locally and at once. What is missing is a "
        "durable producer: a retraction commits nowhere and, when it is the only "
        "thing in a reading, writes no receipt either, so the local effect it "
        "was supposed to have has nothing to be read back from here. Until the "
        "daily cycle — which holds the retraction itself — supplies it, a claim "
        "withdrawn by a retraction and by no later reading reads as `CURRENT`, "
        "and that is a gap in this answer rather than a fact about the claim"),
}


class Refused(RuntimeError):
    """This module will not answer. The alternative is a confident wrong label."""


RECALL_PRODUCED = {
    "schema": "this module's own schema string",
    "index": "how many receipts were read, how many claim ids they name, and "
             "how many cards those claims live in — the denominator of every "
             "verdict below, so that an empty answer is never confused with an "
             "answer about nothing",
    "claims": "one row per claim id, from RECALL_PRODUCED_CLAIM",
    "counts": "how many claims came back under each verdict, including the "
              "verdicts no claim came back under — a count that omits the zeros "
              "reads as 'this case cannot happen'",
    "provider": "whether a memory provider was in the path for this answer, "
                "which is `false` and is stated rather than implied",
    "unobserved": "the axes this filter cannot see, from UNOBSERVED",
    "rule": "which rule decided `recallable`, by name, and what it was given",
}

RECALL_PRODUCED_CLAIM = {
    "id": "the claim id, read off the receipt and confirmed against the line "
          "the card carries",
    "card": "the card that owns the claim's line, from the receipt's `written`",
    "recallable": "`daily_cycle.invalidate_claims`'s answer, restated on the row "
                  "so that a consumer reads one field instead of joining two",
    "span": "the capture object and the two positions the claim was read from, "
            "from the proposal key the receipt is filed under",
    "verdict": "one of VERDICTS, chosen by PRECEDENCE from the observed facts",
    "mark": "that verdict in the doc's own vocabulary, from MARK",
    "why": "the sentence a person reads to know what to do about it",
    "recallable": "`daily_cycle.invalidate_claims`'s answer for this claim — "
                  "the rule's verdict, not this module's. It agrees with "
                  "`verdict == CURRENT` or this module refuses to emit",
    "source_revision": "the identity the receipt recorded for the span, the one "
                       "the source holds now, and why the second is absent when "
                       "it is",
    "withdrawal": "the receipts that named this claim as replaced, and whether "
                  "the line actually left its card",
    "still_in_card": "whether the card the receipt names carries the line now",
    "card_changed_since": "whether the card's digest differs from what the last "
                          "write of it recorded. A fact about the CARD, not "
                          "about this line: see UNOBSERVED.line_text",
}


def _sibling(filename: str):
    """A module in this directory, loaded once and shared with the CLI.

    The same pattern `runtime/cli.py` uses, and for the same reason: three
    modules that each loaded their own copy of `understanding_semantic` would
    have three `Refused` classes, and a caller catching one would not catch
    another's.
    """
    name = "serveros_intake_" + filename[:-3]
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().with_name(filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_semantic = _sibling("understanding_semantic.py")
_delta = _sibling("understanding_delta.py")
_cycle = _sibling("daily_cycle.py")


# --------------------------------------------------------------------------
# the span: the unit of the revision comparison
# --------------------------------------------------------------------------

def span_key(manifest, low, high) -> str | None:
    """One span's name in the revision vocabulary — the span, never the file.

    `None` when the span is not nameable, which is not the same as "the same as
    before": a claim read from a range with no positions has nothing to take a
    digest over, and `understanding_delta.range_content_digest` says so in its
    own words. `None` becomes `UNRESOLVED`, because nothing was compared.
    """
    if not manifest or low is None or high is None:
        return None
    return f"{manifest}#{low}-{high}"


def _read_receipts(base: Path) -> list[dict]:
    """Every receipt under the understanding layer, in a fixed order.

    The receipts are the whole of the durable record: nothing lists them, so
    they are found by name. One this module cannot read is a REFUSAL and not a
    skip — an index missing a receipt is an index that will report a claim
    somebody withdrew as recallable, and there is no partial answer to "may I
    use this" that is safer than saying nothing.
    """
    directory = base / ".serveros"
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.glob("receipt-*.json")):
        try:
            document = _semantic._read_receipt(path)
        except _semantic.Refused as exc:
            raise Refused(
                f"{exc}. This module reads the receipts as the durable record "
                f"of what was written, so a receipt it cannot read is a hole in "
                f"the index — and an index with a hole in it answers "
                f"`recallable` for a claim it never saw") from None
        if document is None:
            raise Refused(
                f"{path} exists and does not hold a receipt. An empty or "
                f"non-object file in the receipts directory is not the absence "
                f"of a receipt, and treating it as absent is how a committed "
                f"claim goes missing from the index")
        found.append({"path": path, "receipt": document})
    return found


def _carried(card: Path) -> tuple[set[str], str | None]:
    """The claim ids a card carries now, and its digest — or why neither is known.

    Read through `understanding_semantic.claims_region` and `_line_id`, the same
    two functions the writer merges on. A second reader of the block would be a
    second opinion about what a card holds, and the disagreement would surface
    as a claim this module cannot match in a card the writer just wrote.
    """
    try:
        text = card.read_text(encoding="utf-8")
    except OSError:
        return set(), None
    body = _semantic.fence_parts(text)[1]
    region = _semantic.claims_region(body)
    if region is None:
        return set(), _semantic.card_digest(text)
    ids = set()
    for line in region[1].splitlines():
        claim_id = _semantic._line_id(line)
        if claim_id is not None:
            ids.add(claim_id)
    return ids, _semantic.card_digest(text)


def build_index(records_root: Path | str) -> dict:
    """What the durable record says about every claim, with the cards read.

    The index is a reading of two things that can disagree — the receipts and
    the cards — and it keeps both rather than merging them. Merging would decide
    which is right, and that decision is exactly what a `CONFLICT`, a `MISSING`
    or a `SUPERSEDED` verdict is for.
    """
    base = _semantic.understanding_root(records_root)
    if not base.is_absolute():
        raise Refused(
            f"{base} is not absolute; a records root relative to whatever "
            f"directory the caller stood in is an index nobody else can rebuild")

    claims: dict[str, dict] = {}
    receipts = _read_receipts(base)
    # By time, then by name — and this order is load-bearing rather than tidy.
    # See `_carried_forward` below: which write PUT a line in a card is read out
    # of the sequence of writes, so a sequence in any other order answers it
    # wrongly. `_read_receipts` finds the files by name, and a receipt's name is
    # a hash of its key, which orders them by nothing a reader can see.
    ordered = sorted(receipts,
                     key=lambda e: (str(e["receipt"].get("committed_at") or ""),
                                    str(e["path"].name)))
    #: Per card, the ids the last write of that card left it carrying.
    carried_before: dict[str, set[str]] = {}
    for entry in ordered:
        receipt = entry["receipt"]
        key = list(receipt.get("key") or [])
        # Five parts, and the fifth is the source's content identity at the
        # moment of the read. A receipt without it is one whose claims cannot be
        # compared against anything, and comparing them against nothing would
        # report every one of them UNRESOLVED for a reason that is about the
        # record and not about the source.
        if len(key) != 5:
            raise Refused(
                f"{entry['path']} carries a proposal key of {len(key)} parts, "
                f"not five. `commit_key` writes the card, the capture object, "
                f"the two positions and the content identity, and a key missing "
                f"the identity is a receipt whose claims this module cannot "
                f"compare against the source")
        card, manifest, low, high, identity = key
        written_at = receipt.get("committed_at")
        for written in receipt.get("written") or []:
            role = written.get("role")
            if role not in (_semantic.OWNER, _semantic.VIEW):
                # The role is the record's own statement about whether this card
                # holds the claim or points at it. A third value, or none, is a
                # record this module cannot read — and reading it as an owner
                # anyway is exactly the failure the field was added to end.
                raise Refused(
                    f"{entry['path']} describes its write to "
                    f"{written.get('card')!r} with role {role!r}, which is "
                    f"neither of this module's own two. A card either owns the "
                    f"claims written into it or references them, and a reader "
                    f"that guessed would put a claim in every card that "
                    f"mentions it")
            owner = written.get("card")
            # `written[i].claims` is what the card CARRIES after this write, and
            # a card carries every commit that ever landed in it — so reading it
            # as "what this write wrote" would give a claim the span of the
            # newest commit to touch its card, whatever span that commit read.
            # A later reading of a neighbouring range would then decide whether
            # an older claim is still current, and it would decide it about a
            # text that claim was never read from. `_carried_forward` is the
            # subtraction that recovers the write.
            put_here = _carried_forward(carried_before, owner,
                                        written.get("claims") or [])
            for claim_id in written.get("claims") or []:
                row = _row(claims, claim_id)
                if role == _semantic.OWNER:
                    # Ownership is not per write: a card that carries a line
                    # owns it however the line got there.
                    row["owners"][owner] = True
                else:
                    row["referenced_by"][owner] = True
                if claim_id not in put_here:
                    continue
                if role == _semantic.OWNER:
                    row["spans"][(card, manifest, low, high)] = True
                    # Owners only. Which revision a claim was read from is the
                    # question `recorded` answers, and only the card that owns
                    # the line was read FROM: a view carries a reference to a
                    # claim whose text it never held, so a view's entry here
                    # would report that claim as having been read from the span
                    # of whatever commit happened to write the view.
                    row["written_by"].append({
                        "receipt": str(entry["path"].name), "card": owner,
                        "after": written.get("after"), "identity": identity,
                        "committed_at": written_at,
                    })
            for claim_id in written.get("removed") or []:
                _row(claims, claim_id)["removal_landed"] = True
        # One entry per receipt that named this claim as replaced, carrying
        # whether that receipt's removal reached a line. The landing is read off
        # the receipt's OWN `superseded_unmet` rather than re-derived from
        # `written[*].removed`: the producer computes it as `set(superseded) -
        # found`, so reading both fields would put one receipt in `replaced_by`
        # twice — once as having removed the line, once as having failed to —
        # and a withdrawal list that contradicts itself is one a person has to
        # adjudicate before they can act on it.
        unmet = set(receipt.get("superseded_unmet") or [])
        for claim_id in receipt.get("superseded") or []:
            _row(claims, claim_id)["replaced_by"].append({
                "receipt": str(entry["path"].name),
                "committed_at": written_at,
                "removal_landed": claim_id not in unmet,
            })

    for row in claims.values():
        # By time, not by receipt name: a receipt's name is a hash of its key,
        # which orders the receipts by nothing a reader can see. The latest
        # write is the one the card's current text came from, so it is the one
        # whose identity a comparison has to be against.
        row["written_by"].sort(key=lambda e: (str(e.get("committed_at") or ""),
                                              e["receipt"]))
        row["replaced_by"].sort(key=lambda e: (str(e.get("committed_at") or ""),
                                               e["receipt"]))

    cards: dict[str, dict] = {}
    for row in claims.values():
        for card in row["owners"]:
            if card not in cards:
                carried, digest = _carried(Path(records_root) / card)
                cards[card] = {"carried": carried, "digest": digest}
    return {"base": base, "receipts": receipts, "claims": claims, "cards": cards}


def _carried_forward(carried_before: dict[str, set[str]], card: str,
                     carried) -> set[str]:
    """The ids this write PUT in a card — the ones the last write did not leave.

    One subtraction over the sequence of writes, and it is the whole of the
    difference between "what this commit wrote" and "what the card carries".
    The set is updated here, so the next write of the same card is compared
    against this one's result rather than against the beginning of time: an id
    that a supersede removes and a later reading puts back is a NEW write of it,
    and comparing against everything ever carried would call that line's span
    the old one.
    """
    ids = {str(claim_id) for claim_id in carried}
    put = ids - carried_before.get(card, set())
    carried_before[card] = ids
    return put


def _row(claims: dict, claim_id: str) -> dict:
    """One claim's accumulating row. Every field is filled by the caller."""
    return claims.setdefault(claim_id, {
        "id": claim_id, "owners": {}, "referenced_by": {}, "spans": {},
        "written_by": [], "replaced_by": [], "removal_landed": False,
    })


# --------------------------------------------------------------------------
# the verdict
# --------------------------------------------------------------------------

def _verdict(row: dict, cards: dict, recorded, digest_now,
             digest_why) -> tuple[str, str]:
    """One claim's verdict and the sentence a person reads, by PRECEDENCE.

    The facts are gathered by the caller and the ORDER is here, in one place,
    because a precedence split across the branches that produce each fact is a
    precedence nobody can read.
    """
    owners = sorted(row["owners"])
    if len(row["spans"]) > 1:
        where = ", ".join(
            f"`{manifest}` 位置 {low}–{high}" for _, manifest, low, high
            in sorted(row["spans"], key=repr))
        return CONFLICT, (
            f"the record writes this id from more than one span ({where}), so "
            f"one id names two claims. A supersede, a conflict and a retraction "
            f"all name an id, and none of them can be resolved through this one")
    if len(owners) > 1:
        # Reachable from a record the writer cannot make but a damaged one can
        # hold — two entries claiming ownership of one line. This is not the
        # same as "two spans": here both entries name one reading, so nothing in
        # the record says which card is the one the claim lives in.
        return CONFLICT, (
            f"the record puts this id in {' and '.join(f'`{o}`' for o in owners)}, "
            f"so one id names two claims in two cards. There is no rule that "
            f"decides which card a reader should be sent to")
    if row["replaced_by"]:
        if row["removal_landed"]:
            return SUPERSEDED, (
                "a later reading named this claim as replaced and its line left "
                "the card. Recalling it would put a replaced fact back into use")
        return SUPERSEDED, (
            f"a later reading named this claim as replaced and its line is STILL "
            f"in `{owners[0]}`, reading as current — no write that commit "
            f"carried found it. Nothing may recall it, and the card wants "
            f"looking at")
    carried = cards.get(owners[0], {}).get("carried")
    if carried is not None and row["id"] not in carried:
        return MISSING, (
            f"`{owners[0]}` carries no line for this id and nothing in the "
            f"record accounts for its absence. The claim is not where it was "
            f"put: a line removed by hand, or a write that did not happen")
    if not recorded:
        # A reading whose `content_identity` is null is a reading that never
        # identified the text it read — the reader emits exactly that when a
        # capture has no messages file to digest. The line is where the writer
        # left it, and there is no revision to compare the source against, so
        # `STALE` would be a claim about a comparison that never happened.
        return UNRESOLVED, (
            "the reading this claim was written from never identified the "
            "content it read — its `content_identity` is absent — so there is "
            "no recorded revision to compare this source against. Nothing was "
            "compared, and nothing here says the text changed")
    if digest_now is None:
        return UNRESOLVED, (
            f"the span this claim was read from cannot be identified now: "
            f"{digest_why}. Nothing was compared, so this is not a statement "
            f"that the text changed — it is the absence of one")
    if digest_now != recorded:
        return STALE, (
            f"the text at `{owners[0]}`'s source, positions "
            f"{sorted(row['spans'])[0][2]}–{sorted(row['spans'])[0][3]}, no longer "
            f"digests to what this claim was read from. The claim is about a "
            f"text that is not there any more; it has to be read again before "
            f"anybody acts on it")
    return CURRENT, (
        "the span still digests to what it digested to, the card carries the "
        "line, and no reading has replaced it")


# --------------------------------------------------------------------------
# the recall
# --------------------------------------------------------------------------

def recall(records_root: Path | str, *, only: list[str] | None = None) -> dict:
    """What may still be recalled, and for everything else, exactly why not.

    `only` restricts the answer to some ids and does NOT restrict the index, the
    cards it reads or the digests it takes: a filter asked about one claim must
    give the same answer it would give about all of them, or the answer depends
    on the question.
    """
    built = build_index(records_root)
    claims = built["claims"]
    if only:
        unknown = sorted(set(only) - set(claims))
        if unknown:
            raise Refused(
                f"{', '.join(unknown)} — no receipt names "
                f"{'this id' if len(unknown) == 1 else 'these ids'}. An id the "
                f"durable record does not hold is not a claim that was "
                f"withdrawn; it is a question about a claim this records tree "
                f"has no record of making")

    # One digest per span, not per claim: two claims read from one span are two
    # claims about ONE text, and taking the digest twice would let a source that
    # changed between the two reads produce two answers about the same span.
    spans: dict[tuple, tuple[str | None, str]] = {}
    for row in claims.values():
        for span in row["spans"]:
            if span not in spans:
                _, manifest, low, high = span
                key = span_key(manifest, low, high)
                if key is None:
                    spans[span] = (None, (
                        "the claim names no capture object or no positions, so "
                        "there is no span to take a digest over"))
                else:
                    spans[span] = _delta.range_content_digest(
                        _delta.messages_file(manifest, Path(records_root)), low, high)

    # The current revisions, in the vocabulary `invalidate_claims` compares in.
    # A span that could not be identified is ABSENT rather than mapped to a
    # placeholder, which is what makes that rule report it as unresolved instead
    # of as unchanged.
    current: dict[str, str] = {}
    rows = []
    per_claim = {}
    for claim_id, row in sorted(claims.items()):
        if not row["owners"]:
            # Nothing OWNS this id as far as the record goes. Two shapes, one
            # repair: either a reading named it as replaced without ever writing
            # it, or a view carries a reference to a claim no card holds. Both
            # mean somebody has to read the record, and neither may be dropped —
            # silence here makes a lost claim look like a claim never made.
            per_claim[claim_id] = _orphan(claim_id, row)
            rows.append({"source_revisions": {}, "verification": NOT_EVIDENCED,
                         "withdrawn": False})
            continue
        span = sorted(row["spans"], key=repr)[0]
        key = span_key(span[1], span[2], span[3])
        recorded = row["written_by"][-1]["identity"]
        digest_now, digest_why = spans[span]
        if key is not None and digest_now is not None:
            current[key] = digest_now
        verdict, why = _verdict(row, built["cards"], recorded, digest_now, digest_why)
        carried = built["cards"].get(sorted(row["owners"])[0], {}).get("carried")
        per_claim[claim_id] = {
            "id": claim_id,
            "card": sorted(row["owners"])[0],
            "span": {"manifest": span[1], "from_position": span[2],
                     "to_position": span[3]},
            "verdict": verdict,
            "mark": MARK[verdict],
            "why": why,
            "source_revision": {
                "recorded": recorded,
                "current": digest_now,
                "why": digest_why,
            },
            "withdrawal": row["replaced_by"] or None,
            "still_in_card": (None if carried is None else claim_id in carried),
            "card_changed_since": _card_changed_since(built, sorted(row["owners"])[0],
                                                      row),
        }
        rows.append({
            "source_revisions": {key: recorded} if key else {},
            "verification": (EVIDENCED if verdict in (CURRENT, STALE, UNRESOLVED)
                             and per_claim[claim_id]["still_in_card"]
                             else NOT_EVIDENCED),
            "withdrawn": verdict == SUPERSEDED,
        })

    judged = _cycle.invalidate_claims(rows, current)
    for claim_id, decision in zip(sorted(claims), judged):
        recallable = decision.get("recallable") is True
        if recallable != (per_claim[claim_id]["verdict"] in RECALLABLE_VERDICTS):
            raise Refused(
                f"{claim_id}: `daily_cycle.invalidate_claims` answered "
                f"recallable={recallable} while this module's own facts say "
                f"{per_claim[claim_id]['verdict']}. The rule decides what may be "
                f"recalled; a label that disagrees with it would be a second "
                f"answer to the one question a consumer is about to act on")
        per_claim[claim_id]["recallable"] = recallable

    chosen = ([per_claim[i] for i in sorted(per_claim)]
              if not only else [per_claim[i] for i in sorted(set(only))])
    for claim in chosen:
        _semantic.no_unsourced(claim, RECALL_PRODUCED_CLAIM, f"claim {claim['id']}")
    counts = {verdict: 0 for verdict in VERDICTS}
    for row in chosen:
        counts[row["verdict"]] += 1
    return _semantic.no_unsourced({
        "schema": SCHEMA,
        "index": {"receipts": len(built["receipts"]),
                  "claims": len(claims),
                  "cards": len(built["cards"])},
        "claims": chosen,
        "counts": counts,
        "provider": {
            "in_the_path": False,
            "consulted": False,
            "because": UNOBSERVED["provider"],
        },
        "unobserved": UNOBSERVED,
        "rule": {
            "recallable_decided_by": "daily_cycle.invalidate_claims",
            "given": {
                "revisions_compared": len(current),
                "vocabulary": {"verification": [EVIDENCED, NOT_EVIDENCED],
                               "withdrawn": "true only for a claim a later "
                                            "reading named as replaced"},
            },
        },
    }, RECALL_PRODUCED, "recall")


def _orphan(claim_id: str, row: dict) -> dict:
    """A claim the record names in some way and no card owns."""
    views = sorted(row["referenced_by"])
    if views:
        why = (f"`{'`, `'.join(views)}` reference{'s' if len(views) == 1 else ''} "
               f"this id and no card owns it. A reference to a claim nothing "
               f"holds is a dangling reference: either the owning card's write "
               f"is missing from this tree or the reference was written by "
               f"mistake, and from here the two look the same")
    else:
        why = (f"{len(row['replaced_by'])} reading(s) name this id as replaced "
               f"and no receipt in this tree ever wrote it. Either an earlier "
               f"commit's receipt is missing or the id was never claimed; the "
               f"two look the same from here, and both want the record read")
    return {
        "id": claim_id,
        "card": None,
        "span": {"manifest": None, "from_position": None, "to_position": None},
        "verdict": MISSING,
        "mark": MARK[MISSING],
        "why": why,
        "source_revision": {
            "recorded": None,
            "current": None,
            "why": ("no reading of this id is recorded, so there is no span to "
                    "compare against"),
        },
        "withdrawal": row["replaced_by"] or None,
        "still_in_card": None,
        "card_changed_since": None,
    }


def _card_changed_since(built: dict, card: str, row: dict) -> bool | None:
    """Whether the card's text differs from what its last write recorded.

    `None` when the card cannot be read at all, which is a fact about the card
    and not about this claim. A card that a person edited is the ordinary case —
    the fence exists so that somebody disputing a claim can write next to it —
    so this NEVER changes a verdict; it is here so that `CURRENT` is not read as
    "this card is byte-for-byte what the writer left".
    """
    digest = built["cards"].get(card, {}).get("digest")
    if digest is None:
        return None
    after = [entry["after"] for entry in row["written_by"]
             if entry["card"] == card and entry.get("after")]
    if not after:
        return None
    return digest != after[-1]


def render_recall(document: dict) -> list[str]:
    """The recall as the lines a person reads, one claim per line."""
    lines = [f"索引  收据 {document['index']['receipts']} 张，主张 "
             f"{document['index']['claims']} 条，涉及卡片 {document['index']['cards']} 张"]
    for row in document["claims"]:
        span = row["span"]
        where = (f"{span['manifest']} {span['from_position']}–{span['to_position']}"
                 if span["manifest"] and span["from_position"] is not None
                 else "没有位置可读")
        lines.append(f"  {row['id']:<12} {row['mark']:<6} "
                     f"{row['card'] or '没有任何卡片持有它'}")
        if row["why"]:
            lines.append(f"      {row['why']}")
        revision = row["source_revision"]
        lines.append(f"      来源 {where}  记录 "
                     f"{(revision['recorded'] or '无')[:12]}  现在 "
                     f"{(revision['current'] or '无法识别')[:12]}")
        if row["card_changed_since"]:
            lines.append("      卡片在最后一次写入之后被改过（是卡片，不是这一行）")
    counts = ", ".join(f"{verdict} {count}"
                       for verdict, count in document["counts"].items() if count)
    lines.append(f"  合计  {counts or '没有任何主张'}")
    lines.append("  未观察：" + " / ".join(document["unobserved"])
                 + " —— 见 --json 的 unobserved；这些轴没有被这条答案检查过")
    return lines

