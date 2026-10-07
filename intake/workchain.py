#!/usr/bin/env python3
"""§6/§8 — the personal board, and the documents behind each row.

WHY THIS IS NOT `global_board.py`
---------------------------------
`global_board.py` answers one question: given the per-project projections the
control plane writes, what is the state of everything. It is a projection of
the authority and nothing else. This module answers a different question —
*what does a person read, in what order, and which document is missing* — and
the difference is that half its input is documents a human wrote.

Keeping them apart matters for the reason `board-contract.yaml` gives: a
projection that can be edited is a second authority. The global board must stay
un-editable. The personal board reads a file a person DID edit (`today.yaml`),
and folding that into the global board would make "derived" stop meaning
anything for both of them.

WHAT IT READS, AND THE ONE THING IT MUST NOT
--------------------------------------------
Three inputs, three authorities:

  * `state/boards/<project>/current.json` — `serveros/board.v3`, written by the
    work-engine control plane. This is the ONLY path by which Item state
    reaches here, which is the boundary `board-contract.yaml` and
    `fleet/mcp-contract.yaml` both draw. There is no sqlite connection in this
    file and there must not be one: a second reader of the store is a second
    answer to "what state is this Item in".
  * `records/projects/<project>/items/<ID>/*` — the documents a person wrote.
  * `records/projects/<project>/board/today.yaml` — the selection a person made.

THE THREE STATES A COLUMN CAN BE IN, AND WHY ALL THREE ARE VISIBLE
------------------------------------------------------------------
A priority is a level, `UNPRODUCED`, or `UNREADABLE`. A document is `PRESENT`,
`MISSING`, `NOT_YET_REQUIRED` or `UNREADABLE`. In both cases the distinctions
are load-bearing:

  * `UNPRODUCED` and `UNREADABLE` are different facts. Nothing declared a
    priority; something declared one and the file would not parse. Rendering
    both as blank would hide a broken card behind a missing decision.
  * `MISSING` and `NOT_YET_REQUIRED` are different facts. An ACTIVE Item with
    no `design.md` is a gap; an INBOX Item with no `design.md` is an Item that
    has not started. Collapsing them makes every new Item look broken, and a
    board where everything is amber is a board nobody reads.

A MISSING PROJECTION IS NOT AN EMPTY PROJECT
--------------------------------------------
If `state/boards/<project>/current.json` is absent, the project is reported as
`UNREAD`, with the path that was looked for. It is not reported as a project
with no Items. This is the same rule the observation envelope applies to a
timestamp: "nobody looked" and "looked, found nothing" produce the same blank
in a payload and are different facts.

FOUR PATH DEFECTS, AND THE ONE THEY ALL WERE
--------------------------------------------
A card locator was ignored; `design.md` was named where the records hold
`design/`; `<records>/<project>/` was composed in four places where the real
layout is `<records>/projects/<project>/`; and `records/projects/<p>/context/
understanding.md` was declared for a layer that actually lives at
`records/information/understanding/` in four scopes. None crashed. Each
produced a confident, plausible, wrong sentence about somebody's real work —
17 cards missing while they sat on the disk, every design reported unwritten,
a `today.yaml` written where nothing reads it, and an understanding layer
reported as absent. The response is not four patches but one rule, which the
rest of this file is arranged around: **a locator is resolved, never composed,
and when it cannot be resolved the answer says which rule was tried and where
it landed.**

THE CHECKER HAD THE SAME DEFECT ONE FIELD OVER
-----------------------------------------------
`check_skeleton` first reported a document `INCOMPLETE` when it lacked the
headings `document-skeletons.yaml` proposes. Run against this node it called 21
real briefs five-of-five incomplete, because real briefs use headings of their
own. That is a rule written in a repository and reported as a fact about the
world, which is the same defect as the four above with a different noun. The
states are now `MATCHES`, `DIVERGES`, `NOT_CHECKED` and `EMPTY`, and only
`EMPTY` is a defect, because an empty file is a fact rather than a standard.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import yaml

SCHEMA = "serveros/personal-board.v1"
CONTRACT_SCHEMA = "serveros/workchain-contract.v1"
SKELETON_SCHEMA = "serveros/document-skeletons.v1"
BOARD_SCHEMA = "serveros/board.v3"

UNPRODUCED = "UNPRODUCED"
UNREADABLE = "UNREADABLE"
UNKNOWN = "UNKNOWN"
UNBOUND = "UNBOUND"

PRESENT = "PRESENT"
MISSING = "MISSING"
NOT_YET_REQUIRED = "NOT_YET_REQUIRED"

#: A third way a document can be absent, for the reader that has the record and
#: not the control plane. Whether an Item owes a document is a function of its
#: STATE, so a reader holding no state cannot answer it — and `MISSING` and
#: `NOT_YET_REQUIRED` are both answers. The first would call a document overdue
#: on the strength of a state nobody read; the second would excuse it on the
#: same strength. What IS known, and all this says, is whether the file is here.
REQUIREMENT_UNKNOWN = "REQUIREMENT_UNKNOWN"

#: The delivery surface's two faces. `NOT_DECLARED` is not a shortfall and is
#: not the same fact as `UNREADABLE`: a card that states no delivery surface
#: cannot be read as one whose surface is unmet, and neither can be read as one
#: that was never read.
DECLARED = "DECLARED"
NOT_DECLARED = "NOT_DECLARED"

#: The completion boundary's own vocabulary. `NOT_ASSESSED` is here so that
#: "nothing was owed at this state" and "everything owed was found" are two
#: answers rather than one: a check that was never run is not a check that
#: passed.
COMPLETE = "COMPLETE"
DISCHARGED = "DISCHARGED"
OPEN = "OPEN"
NOT_ASSESSED = "NOT_ASSESSED"

PRIORITY_LEVELS = ("P0", "P1", "P2", "P3", "P4")

#: Read from the contract at load time; these are the fallbacks that make a
#: broken contract a loud failure rather than a silently different board.
DEFAULT_DOCUMENTS = (("card.yaml", ()),)

#: The document a candidate needs to be ON the board, and nothing else. §6:
#: "不要求先给每个候选写长需求文档". This tuple is that sentence as code.
ENTRY_DOCUMENT = "card.yaml"


class Refused(Exception):
    """A projection was asked to compose something that is not what it says."""


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------

def load_contract(path: str | Path) -> dict:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if document.get("schema") != CONTRACT_SCHEMA:
        raise Refused(
            f"{path} declares schema {document.get('schema')!r} and not "
            f"{CONTRACT_SCHEMA!r}. A composition rule read from a document "
            f"that is not the one it says it is has no authority at all.")
    return document


def _document_rules(contract: dict) -> list[dict]:
    """(`name`, `resolved_by`, required-at-set) for every document, from the contract.

    The required-at sets come from the contract rather than a literal here, for
    the reason every other contract in this tree exists: a rule that lives in
    two places is a rule that will be changed in one of them, and this is the
    one where the divergence would be invisible — a document that stopped being
    required reads as a document nobody wrote.

    `resolved_by` is here for the same reason and was added because a rule
    written as `design.md` was simply WRONG about this node. The real records
    under `records/projects/<project>/items/<ID>/` hold `brief.md` as a file and
    `design/`, `plan/` and `report/` as DIRECTORIES of documents. A contract
    that named only the `.md` spelling would have reported every design, plan
    and report on this node as unwritten while the files sat on the disk — the
    same class of defect as the locator one, one field over.
    """
    rules = []
    for entry in contract.get("item_documents") or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            raise Refused(
                "an `item_documents` entry names no document, so it declares a "
                "requirement nothing can satisfy and nothing can violate")
        kinds = entry.get("resolved_by") or []
        if not set(kinds) <= {"exact-file", "directory", "suffixed-file"}:
            raise Refused(
                f"`{entry['name']}` declares `resolved_by: {kinds}`, which "
                f"names a resolution rule this module does not have. Reporting "
                f"an unknown rule as absent would call a document missing "
                f"because its spelling was not understood.")
        rules.append({"name": entry["name"], "resolved_by": tuple(kinds),
                      "required_at": set(entry.get("required_at") or ())})
    if not rules:
        raise Refused(
            "`item_documents` is empty, which would make every Item complete. "
            "An empty requirement set is not a lenient rule, it is the "
            "absence of one.")
    return rules


def _resolve_document(item_dir: Path, rule: dict) -> tuple[Path | None, str, int]:
    """Where one document is, and under which of the declared rules.

    Returns `(path, how, count)`. `path` is None when none of the rules match.
    A directory counts as present only when it HOLDS something: an empty
    `design/` is a directory somebody made, which is not the same as a design
    having been written, and reporting it as present would let a `mkdir` close
    a gap.
    """
    name = rule["name"]
    for kind in rule["resolved_by"]:
        if kind == "exact-file":
            candidate = item_dir / name
            if candidate.is_file():
                return candidate, "exact-file", 1
        elif kind == "suffixed-file":
            candidate = item_dir / f"{name}.md"
            if candidate.is_file():
                return candidate, "suffixed-file", 1
        elif kind == "directory":
            candidate = item_dir / name
            if candidate.is_dir():
                held = [p for p in sorted(candidate.iterdir()) if p.is_file()]
                if held:
                    return candidate, "directory", len(held)
    return None, "", 0


# ---------------------------------------------------------------------------
# Which generation of the records a read was taken from
# ---------------------------------------------------------------------------

TREE_FOLLOWING = "FOLLOWING"
TREE_AHEAD = "AHEAD"
TREE_BEHIND = "BEHIND"
TREE_DIVERGED = "DIVERGED"
TREE_UNREAD = "UNREAD"


class TreePosition:
    """Where the tree a read was taken from stands, and what its ref holds.

    THE DEFECT THIS EXISTS FOR
    --------------------------
    On 2026-10-07 `work item SRV-0011` printed

        × brief.md  MISSING  an Item in ACTIVE is expected to have it, and it
                             resolves under none of ['exact-file']

    and the sentence was true of the tree it read and false about the Item.
    `brief.md` had been written, merged to `origin/main` and read back on this
    node; the canonical records checkout here was 13 commits behind the ref that
    carries it, and nothing on the node follows that ref. The reader bound
    itself to a working tree, found no file, and reported the absence as a fact
    about somebody's work — the fifth instance of this module's one defect, the
    other four being the path defects the header lists.

    WHAT IT IS, AND WHAT IT REFUSES
    -------------------------------
    It is read-only and it never fetches. `holds()` asks the git object store
    what the branch's ref contains — a question about bytes this node already
    has. Asking the network instead would make a read verb's answer depend on
    connectivity and on somebody else's push schedule, so a tree whose refs were
    never fetched reports `UNREAD` with that as its reason rather than a lag it
    cannot see.

    `git` is injected rather than imported, for the reason the rest of this
    module takes paths instead of composing them: a projection that cannot run
    without a git binary is a projection no fixture can exercise, and every test
    of the document rules would otherwise start needing a repository.

    `None` FROM `holds()` IS NOT `False`
    ------------------------------------
    A path this cannot ask about — no ref, no git, a path outside the root —
    answers `None`, and a caller must leave its sentence alone rather than
    report an absent document. That third answer is the whole point: it is the
    difference between "the authority does not have it either" and "nobody
    established where it is".
    """

    def __init__(self, *, root, revision=None, branch=None, upstream_ref=None,
                 upstream_revision=None, ahead=None, behind=None,
                 state=TREE_UNREAD, why="", git=None):
        self.root = Path(root) if root else None
        self.revision = revision or None
        self.branch = branch or None
        self.upstream_ref = upstream_ref or None
        self.upstream_revision = upstream_revision or None
        self.ahead = ahead
        self.behind = behind
        self.state = state
        self.why = why
        self._git = git
        self._held: dict[str, bool | None] = {}
        self._head: dict[str, bool | None] = {}
        self._text: dict[str, str | None] = {}
        self._counts: dict[str, int | None] = {}

    def relative(self, path) -> str | None:
        """`path` as the ref spells it, or None when it lies outside the root."""
        if self.root is None:
            return None
        try:
            return str(Path(path).resolve().relative_to(self.root))
        except (ValueError, OSError):
            return None

    def holds(self, relpath: str | None) -> bool | None:
        """Whether `relpath` exists at the upstream ref.

        Exit 128 is git's "no such path at that object" and is the only code
        read as absent; anything else is unanswerable rather than missing. The
        ref is verified before it is stored, so 128 cannot mean "the ref itself
        is bad" — a caller that skipped that check would be reading a broken ref
        as an absent document, which is the same misattribution one level down.
        """
        if relpath is None:
            return None
        if relpath in self._held:
            return self._held[relpath]
        answer: bool | None = None
        if self._git is not None and self.root is not None and self.upstream_ref:
            code, _out = self._git(self.root, "cat-file", "-e",
                                   f"{self.upstream_ref}:{relpath}")
            if code == 0:
                answer = True
            elif code == 128:
                answer = False
        self._held[relpath] = answer
        return answer

    def holds_at_head(self, relpath: str | None) -> bool | None:
        """Whether this tree's OWN HEAD carries `relpath`.

        `holds()` answers for the authority and this answers for the checkout
        doing the reading, and both are needed to keep two situations apart: a
        document this tree never had, and a document somebody removed from it.
        Only the first is the authority's to answer for. Resurrecting a file a
        branch deliberately deleted would be this module inventing a document,
        which is the same defect as reporting the tree's lag as the Item's.

        Read the way `holds()` reads: exit 128 is "not at that object" and
        anything else is unanswerable rather than absent.
        """
        if relpath is None:
            return None
        if relpath in self._head:
            return self._head[relpath]
        answer: bool | None = None
        if self._git is not None and self.root is not None:
            code, _out = self._git(self.root, "cat-file", "-e",
                                   f"HEAD:{relpath}")
            if code == 0:
                answer = True
            elif code == 128:
                answer = False
        self._head[relpath] = answer
        return answer

    def read_text(self, relpath: str | None) -> str | None:
        """The document's text as the authority holds it, or None.

        None covers "the ref does not have it" and "the ref could not be asked"
        alike, because the caller's next move is the same either way. What this
        adds over `holds()` is the point of the method: a reader that reports
        the CAUSE of an absence has explained it, and a reader that returns the
        bytes has closed it. A brief nobody can read is still a brief the Item
        is waiting on, however accurately the wait was described.
        """
        if relpath is None or not self.upstream_ref:
            return None
        if relpath in self._text:
            return self._text[relpath]
        answer: str | None = None
        if self._git is not None and self.root is not None:
            code, out = self._git(self.root, "cat-file", "blob",
                                  f"{self.upstream_ref}:{relpath}")
            if code == 0:
                answer = out
        self._text[relpath] = answer
        return answer

    def count_files(self, relpath: str | None) -> int | None:
        """How many documents sit directly under a directory at the authority.

        `cat-file -e` says the tree EXISTS and says nothing about anybody having
        put anything in it. The rule `_resolve_document` already carries — an
        empty `design/` is a directory somebody made, not a design — has to
        hold at the ref too, or `mkdir` would close the same gap on a second
        surface. Subtrees are not counted, for the reason they are not counted
        in the working tree either: the question is how many documents are in
        there, and a directory inside it is not one.
        """
        if relpath is None or not self.upstream_ref:
            return None
        if relpath in self._counts:
            return self._counts[relpath]
        answer: int | None = None
        if self._git is not None and self.root is not None:
            code, out = self._git(self.root, "ls-tree",
                                  f"{self.upstream_ref}:{relpath}")
            if code == 0:
                entries = [line.split("\t", 1)[0].split()
                           for line in out.splitlines()]
                answer = sum(1 for parts in entries
                             if len(parts) >= 2 and parts[1] == "blob")
        self._counts[relpath] = answer
        return answer


def tree_position(records_root: Path, *, git) -> TreePosition:
    """The records tree's own generation, resolved against the ref it follows.

    `@{upstream}` IS NOT ENOUGH, AND ON THE MAIN RECORDS CHECKOUT IT IS A FATAL.
    On 2026-10-07 `/data/home/vince/workspace/records` had `branch.main.remote`
    and `branch.main.merge` both unset, so `git rev-parse @{upstream}` exited
    128 there and every derivation from it failed. A checkout whose branch
    declares no authority cannot be told it has fallen behind that authority,
    which is how a 13-commit lag sat unnoticed under a verb that reads it every
    day. The fallback to `origin/<branch>` is what makes a position readable at
    all on that tree, and `why` records that the branch declares nothing, so a
    reader can tell a branch that follows its authority from one that merely
    has a same-named remote ref.
    """
    root = Path(records_root)
    code, out = git(root, "rev-parse", "--show-toplevel")
    if code != 0:
        return TreePosition(root=root, why=(
            f"{root} is not a git checkout: `git rev-parse --show-toplevel` "
            f"exited {code}. The documents were still read; only which "
            f"generation of them was read is unknown"), git=git)
    top = Path(out.strip().splitlines()[0]) if out.strip() else root

    code, out = git(top, "rev-parse", "HEAD")
    if code != 0:
        return TreePosition(root=top, why=(
            f"`git rev-parse HEAD` in {top} exited {code}: {out.strip()}"), git=git)
    revision = out.strip()

    _code, out = git(top, "rev-parse", "--abbrev-ref", "HEAD")
    branch = out.strip() or None
    if branch == "HEAD":
        branch = None

    declared = True
    _code, out = git(top, "rev-parse", "--abbrev-ref", "--symbolic-full-name",
                     "@{upstream}")
    ref = out.strip() if _code == 0 else ""
    if not ref:
        declared = False
        ref = f"origin/{branch}" if branch else ""
        if not ref:
            return TreePosition(root=top, revision=revision, branch=None,
                                why=(f"{top} is on a detached HEAD, so there is "
                                     f"no branch whose ref could be followed"),
                                git=git)
        code, _out = git(top, "rev-parse", "--verify", "--quiet",
                         f"refs/remotes/{ref}")
        if code != 0:
            return TreePosition(root=top, revision=revision, branch=branch, why=(
                f"{top} is on {branch}, which declares no upstream "
                f"(`branch.{branch}.remote` and `.merge` are both unset) and "
                f"has no {ref} ref to fall back to. Nothing on this node states "
                f"what this branch is supposed to follow"), git=git)

    code, out = git(top, "rev-list", "--left-right", "--count", f"HEAD...{ref}")
    if code != 0:
        return TreePosition(root=top, revision=revision, branch=branch,
                            upstream_ref=ref, why=(
                                f"`git rev-list --left-right --count HEAD...{ref}` "
                                f"exited {code}, so how far this tree stands from "
                                f"{ref} was not established"), git=git)
    parts = out.split()
    ahead, behind = (int(parts[0]), int(parts[1])) if len(parts) == 2 else (None, None)

    _code, out = git(top, "rev-parse", ref)
    upstream_revision = out.strip() or None

    if ahead is None:
        state = TREE_UNREAD
    elif behind and ahead:
        state = TREE_DIVERGED
    elif behind:
        state = TREE_BEHIND
    elif ahead:
        state = TREE_AHEAD
    else:
        state = TREE_FOLLOWING

    why = ""
    if not declared:
        why = (f"`branch.{branch}.merge` is unset on this checkout, so {ref} is "
               f"the remote-tracking ref a reader falls back to and not one this "
               f"branch follows")
    return TreePosition(root=top, revision=revision, branch=branch,
                        upstream_ref=ref, upstream_revision=upstream_revision,
                        ahead=ahead, behind=behind, state=state, why=why, git=git)


def _document_spellings(rule: dict) -> list[str]:
    """The filenames a rule would accept, in the order it would accept them.

    Shared with `_resolve_document`'s vocabulary on purpose: the question "is it
    at the ref" has to be asked with the same spellings as "is it in the tree",
    or a document could be reported absent at the ref because this looked for
    the one name the rule does not use.
    """
    names = []
    for kind in rule["resolved_by"]:
        if kind == "suffixed-file":
            names.append(f"{rule['name']}.md")
        else:
            names.append(rule["name"])
    return names


def _resolve_from_ref(item_dir: Path, rule: dict, tree,
                      ) -> tuple[str, str, int] | None:
    """Where the authority holds a document this checkout does not have.

    The spellings and their ORDER are `_resolve_document`'s, asked of a second
    surface: a document reported absent at the ref because this looked for a
    name the rule does not use would be the same misattribution one level down.
    Returns `(the path as the ref spells it, how it resolved, how many)`.

    Called only when this checkout is behind the authority AND never carried
    the path. A tree level with the ref has nothing the ref has and it lacks;
    a tree ahead of the ref may have deleted the document on purpose, and
    answering that from the ref would be answering a question about a
    different tree.
    """
    base = tree.relative(item_dir)
    if base is None:
        return None
    base = "" if base == "." else base + "/"
    name = rule["name"]
    for kind in rule["resolved_by"]:
        if kind == "exact-file":
            if tree.holds(f"{base}{name}"):
                return f"{base}{name}", "exact-file", 1
        elif kind == "suffixed-file":
            if tree.holds(f"{base}{name}.md"):
                return f"{base}{name}.md", "suffixed-file", 1
        elif kind == "directory":
            count = tree.count_files(f"{base}{name}")
            if count:
                return f"{base}{name}", "directory", count
    return None


def _authority_bytes(item_dir: Path, rule: dict, tree,
                     ) -> tuple[str, str, int, str | None] | None:
    """What the authority holds for a document this checkout never had.

    The ONE place that decides a lagging checkout may borrow a document from
    the ref. Two readers ask it — the `● name` row in `_documents`, and, since
    2026-10-08, `load_card` for the card itself — and they share the decision
    so that a header and the line directly beneath it cannot come to different
    answers about the same file. That disagreement is not hypothetical: the
    documents row reported `card.yaml PRESENT ← 读自 origin/main@d7f79d80`, and
    the two lines above it reported `UNPRODUCED —— no card` and `卡片
    UNRESOLVED`, because those two had asked a different question of a
    different surface.

    Three conditions have to hold at once, and each stops a different mistake:

      * the tree is BEHIND the ref. Level with it there is nothing to borrow,
        and ahead of it the tree is the newer surface;
      * the tree's own HEAD never carried the path, so a document somebody
        deleted here is not a document nobody materialized;
      * the bytes come back. Presence alone would let a row claim to have been
        read when only its existence was established.

    Returns `(the path as the ref spells it, how it resolved, how many, text)`.
    `text` is None for a `directory` resolution, which holds a SET of documents
    and is not one text to read.
    """
    if tree.state != TREE_BEHIND:
        return None
    found = _resolve_from_ref(item_dir, rule, tree)
    if found is None:
        return None
    relpath, how, count = found
    if tree.holds_at_head(relpath) is not False:
        # Either this tree's HEAD has it — so it was removed from the working
        # tree here, and this tree is the party to ask — or nobody established
        # whether it does. An unestablished question is not a licence to read
        # from somewhere else, which is this module's whole defect.
        return None
    text = None if how == "directory" else tree.read_text(relpath)
    if how != "directory" and text is None:
        return None
    return relpath, how, count, text


def _read_from_the_authority(item_dir: Path, rule: dict, tree, skeletons,
                             name: str) -> dict:
    """The row a lagging checkout borrows from the authority, or nothing.

    Returns the fields to merge when the authority holds a document this tree
    never had, and an empty dict when it does not — in which case the caller
    falls through to the absence it was always going to report. The decision
    itself is `_authority_bytes`'.

    The state becomes PRESENT because the document IS read — the bytes are the
    authority's and the skeleton check runs on them, which is what the previous
    version of this could not do while it reported only the cause. What keeps
    that honest is `read_from` naming the surface on the row itself, and `why`
    saying that nothing was written into the checkout to make it readable.
    """
    found = _authority_bytes(item_dir, rule, tree)
    if found is None:
        return {}
    relpath, how, count, text = found
    which = relpath.rsplit("/", 1)[-1]
    return {
        "state": PRESENT,
        "read_from": tree.upstream_ref,
        "read_from_revision": tree.upstream_revision,
        "resolved_by": how,
        "count": count,
        "held_by_upstream": True,
        "upstream_ref": tree.upstream_ref,
        "upstream_path": relpath,
        "skeleton": check_skeleton(text, skeleton_of(skeletons, name)),
        "why": (
            f"`{which}` is not in the working tree this reader is bound to "
            f"({tree.root} at {str(tree.revision or '?')[:12]}), and it IS "
            f"present at {tree.upstream_ref}"
            + (f", which that tree is {tree.behind} behind"
               if tree.behind else "")
            + f". Read from there: the content is {tree.upstream_ref}'s, and "
              f"nothing was written into that checkout to make it readable"
            + (f", because this branch declares no upstream to tell it so"
               if not tree.branch or "unset" in tree.why else "") + "."),
    }


def _explain_absence_by_tree(answer: dict, item_dir: Path, rule: dict, tree) -> None:
    """Record what the authority holds, and only then say why nothing resolved.

    `held_by_upstream` is set on every branch of this — True, False, or None —
    because "the ref has it and this tree does not" is a fact a JSON consumer
    needs too, and a fact that exists only inside a rendered sentence is not
    one. `why` is rewritten ONLY when the answer is True: for False the existing
    sentence already names the right party, and for None nobody established a
    cause, so claiming one would be this class's own defect.

    This runs only when `_read_from_the_authority` did NOT resolve the
    document, so every case here is one where nothing could be read. Two guards
    on top of the name-level answer, because a sentence about whose fault an
    absence is has to be as narrow as the evidence:

      * the name exists at the ref but not in a shape the rule accepts — an
        empty `design/` is the case this catches, and there the Item is still
        the right party;
      * the tree is not behind, or its own HEAD carries the path — a document
        this checkout removed on purpose is a change here, not a lag there.
    """
    spellings = _document_spellings(rule)
    held = [tree.holds(tree.relative(item_dir / name)) for name in spellings]
    verdict = True if any(h is True for h in held) else (
        False if held and all(h is False for h in held) else None)
    answer["held_by_upstream"] = verdict
    answer["upstream_ref"] = tree.upstream_ref
    if verdict is not True:
        return
    found = _resolve_from_ref(item_dir, rule, tree)
    if found is None:
        return
    relpath = found[0]
    answer["upstream_path"] = relpath
    if tree.state != TREE_BEHIND or tree.holds_at_head(relpath) is True:
        return
    answer["why"] = (
        f"`{relpath.rsplit('/', 1)[-1]}` is not in the working tree this reader is bound to "
        f"({tree.root} at {str(tree.revision or '?')[:12]}), and it IS present "
        f"at {tree.upstream_ref}"
        + (f", which that tree is {tree.behind} behind" if tree.behind else "")
        + f". The document is not missing from the Item — it was never "
          f"materialized into this tree"
        + (f", and this branch declares no upstream to tell it so"
           if not tree.branch or "unset" in tree.why else "") + ".")


# ---------------------------------------------------------------------------
# The skeletons: what a document of each kind is expected to contain.
# ---------------------------------------------------------------------------

def load_skeletons(path: str | Path) -> dict:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if document.get("schema") != SKELETON_SCHEMA:
        raise Refused(
            f"{path} declares schema {document.get('schema')!r} and not "
            f"{SKELETON_SCHEMA!r}. A shape read from a document that is not the "
            f"one it says it is would be applied to somebody's real brief.")
    return document


def skeleton_of(skeletons: dict | None, name: str) -> dict | None:
    """One document's declared shape, or None when nothing declares it."""
    for entry in (skeletons or {}).get("documents") or []:
        if entry.get("id") == name or entry.get("file") == name:
            return entry
    return None


def check_skeleton(text: str | None, skeleton: dict | None) -> dict:
    """What is IN a document, and whether it matches the proposed shape.

    FOUR ANSWERS, and only one of them is a defect:

      * `NOT_CHECKED` — nothing declares this document, or there is no single
        text to read. A `directory` resolution holds a SET of documents written
        separately for different reasons, and joining them to look for headings
        would be a claim this function cannot make.
      * `EMPTY` — no non-whitespace content. Unambiguously a defect, and the
        only one here: an empty file is not a document that chose its own shape.
      * `MATCHES` — every declared heading is present.
      * `DIVERGES` — real content, and not these headings. **Not a defect.**

    The fourth is the one this function got wrong first. It reported a document
    `INCOMPLETE` when it lacked the declared headings, and then it was run
    against this node, where every real `brief.md` uses sections of its own
    (`## 定性`, `## 判据与证据`, `## 背景与问题陈述`) and none uses the proposed
    ones. It called 21 real briefs five-of-five incomplete. A rule invented in a
    contract, reported as a fact about somebody's work, with the alarm sounding
    on every document — which is exactly how a board becomes one nobody reads.

    So `DIVERGES` carries the document's OWN headings beside the missing ones.
    A reader sees "this brief is structured, just not this way" and judges;
    nothing downstream counts it as a gap.

    LENGTH IS NOT MEASURED. The shortest real brief on this node is 106
    characters and answers what/whether-now/boundary. A floor would have
    flagged six real briefs, which is the same false alarm one field over.
    """
    if skeleton is None:
        return {"state": "NOT_CHECKED", "missing": [], "found": [],
                "own_headings": [], "why": ("no skeleton declares this "
                                            "document, so nothing here has an "
                                            "opinion about its contents")}
    if text is None:
        return {"state": "NOT_CHECKED", "missing": [], "found": [],
                "own_headings": [],
                "why": ("the document was not read — it is a directory, or it "
                        "is absent — so its contents were not checked. This is "
                        "the absence of a check and not a check that failed")}
    if not skeleton.get("sections"):
        # A skeleton that declares no headings has none to be missing, and the
        # empty `missing` that follows would report MATCHES on any file with a
        # single character in it. `understanding` is declared this way on
        # purpose: its four scopes have no common shape to be checked against,
        # so the honest answer is that nothing is checked.
        return {"state": "NOT_CHECKED", "missing": [], "found": [],
                "own_headings": [],
                "why": (f"`{skeleton.get('id')}` declares no headings — the "
                        f"shape of this document was observed to differ by "
                        f"scope, so there is nothing here to check against")}
    if not text.strip():
        return {"state": "EMPTY", "missing": [s.get("heading") for s in
                                              skeleton.get("sections") or []],
                "found": [], "own_headings": [],
                "why": ("the file holds no content at all. Every document is "
                        "required to be something; this is the one state here "
                        "that is a defect rather than a difference")}
    own = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            own.append(stripped.lstrip("#").strip())
    missing, found = [], []
    for section in skeleton.get("sections") or []:
        heading = str(section.get("heading") or "")
        (found if heading in own else missing).append(heading)
    if not missing:
        return {"state": "MATCHES", "missing": [], "found": found,
                "own_headings": own,
                "why": f"all {len(found)} declared headings are present"}
    return {"state": "DIVERGES", "missing": missing, "found": found,
            "own_headings": own,
            "why": (f"the document is written and uses its own "
                    f"{len(own)} heading(s); {len(missing)} of the "
                    f"{len(missing) + len(found)} PROPOSED headings "
                    f"({', '.join(missing)}) are not among them. This is a "
                    f"difference and not a defect — the proposed shape is "
                    f"`PROPOSED_NOT_APPROVED` and no document on this node uses "
                    f"it")}


def read_document_text(path: Path | None, how: str) -> str | None:
    """The text to check, or None when there is nothing single to read.

    A `directory` resolution holds a SET of documents and is not one text, so it
    returns None and the checker reports `NOT_CHECKED` rather than inventing a
    joined view of files that were written separately for different reasons.
    """
    if path is None or how == "directory":
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _read_yaml(path: Path) -> tuple[dict | None, str | None]:
    """(document, why-not). An unreadable card is not an absent one."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "not present"
    except OSError as exc:
        return None, f"unreadable: {exc}"
    return _parse_yaml_text(text)


def _parse_yaml_text(text: str) -> tuple[dict | None, str | None]:
    """(document, why-not) for text already in hand.

    Split out from `_read_yaml` because two surfaces now hand this function
    bytes: the working tree's own file, and the ref a lagging checkout borrows
    from. The verdict about the CONTENT has to be the same both ways — a card
    that does not parse is a card that does not parse wherever it was read —
    and what differs is only which surface the caller names on the row.
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, f"does not parse as YAML: {exc.__class__.__name__}"
    if not isinstance(document, dict):
        return None, f"parses as {type(document).__name__}, not a mapping"
    return document, None


def _read_projection(path: Path) -> tuple[dict | None, str | None]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "the control plane has not written this projection"
    except OSError as exc:
        return None, f"unreadable: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"does not parse as JSON: {exc.msg}"
    if not isinstance(document, dict):
        return None, f"parses as {type(document).__name__}, not an object"
    return document, None


def resolve_aliases(names, aliases) -> tuple[list[str], dict[str, list[str]]]:
    """别名换成它归属的项目名。返回 `(要读的名字, 谁被换成了谁)`。

    `litellm-observability` 不是一个项目，是 `litellm` 的**另一个名字**（见
    `projects.yaml` 的 `project_aliases`，那里写了为什么它不能是一个项目）。
    所以一个同时点名 `litellm` 和 `litellm-observability` 的调用者，要的是
    **一份**投影而不是两份：读两次同一个 `current.json` 会让同一个 Item 在组合
    视图里出现两行，而 `select_focus` 对重复 ID 是抛错的 —— 重复不会静静地
    过去，它会让整个周期算不出来。于是折叠发生在这里，而不是在它下游。

    返回的第二项按「被读到的项目」归并，因为它是给**那一行**用的：一行要说得出
    它同时回答了哪几个被点名的名字。一个调用者点名三个项目、拿到两份投影，
    这个差是调用者要知道的事，不是这一步该替它咽下去的事。

    别名表里没有的名字原样放行 —— 包括一个指错方向的别名。那不是「换个名字」，
    是一个坏掉的绑定，而它的下游表现是一个实实在在的 UNREADABLE 行（「这个名字
    下没有投影」），不是被悄悄读成别的项目。绑定本身对不对由 ops 的
    `test_a_project_alias_names_a_registered_project_and_issues_no_ids` 钉住：
    注册表缺陷是注册表测试的事，不是运行期猜的事。
    """
    resolved: list[str] = []
    renamed: dict[str, list[str]] = {}
    for requested in names:
        entry = aliases.get(requested)
        target = (entry or {}).get("project") if isinstance(entry, dict) else None
        name = target if target and target != requested else requested
        if name != requested:
            renamed.setdefault(name, []).append(requested)
        if name not in resolved:
            resolved.append(name)
    return resolved, renamed


def load_projects(*, boards_root: Path, projects=None, aliases=None) -> list[dict]:
    """Every project's projection, as `{project, document, state, why, path}`.

    A project named by `projects` and absent from the disk is UNREAD and stays
    in the list. Dropping it would make "this project has no Items" and "this
    project is not on this node" the same row, which is the collapse this
    module spends most of its length avoiding.

    `aliases` is the registry's `project_aliases` (name → `{project, …}`), and
    it applies only when the caller NAMES projects. The other branch walks the
    disk, and a directory named `litellm-observability` is not a thing a
    registry entry could create — so an alias there would be answering a
    question nobody asked, which is indistinguishable from inventing a project.
    """
    renamed: dict[str, list[str]] = {}
    found = []
    if projects:
        projects, renamed = resolve_aliases(projects, aliases or {})
        candidates = [(name, boards_root / name / "current.json") for name in projects]
    else:
        candidates = [(path.parent.name, path) for path in sorted(boards_root.glob("*/current.json"))]
        if not candidates and boards_root.is_dir():
            # The directory exists and holds no projection. That is a fact
            # about the control plane, and it is reported as one below rather
            # than as an empty board.
            candidates = []
    for name, path in candidates:
        document, why = _read_projection(path)
        if document is None:
            found.append({"project": name, "state": UNREADABLE, "why": why,
                          "path": str(path), "document": None,
                          "also_named": list(renamed.get(name) or [])})
            continue
        schema = document.get("schema")
        if schema != BOARD_SCHEMA:
            found.append({"project": name, "state": UNREADABLE,
                          "why": (f"declares schema {schema!r} and not "
                                  f"{BOARD_SCHEMA!r}; a board read from a "
                                  f"document that is not the one it says it is "
                                  f"has no authority"),
                          "path": str(path), "document": None,
                          "also_named": list(renamed.get(name) or [])})
            continue
        found.append({"project": name, "state": "READ", "why": "",
                      "path": str(path), "document": document,
                      "also_named": list(renamed.get(name) or [])})
    return found


def project_dir(records_root: Path, project: str) -> Path:
    """`<records_root>/projects/<project>` — the segment, named once.

    The `projects/` level is REAL on this node:
    `records/projects/litellm/{items,board,context,architecture,reports}`. A
    first version composed `<records_root>/<project>/...` in four places, and
    every one of them was wrong in the same direction and silently: `today.yaml`
    written where nothing reads it, `sources.yaml` read where nobody writes it,
    and a card fallback pointed one level above the cards. Each of the four
    reported a plausible state — "nobody chose", "no list was authored", "no
    card is there" — for a fact that was only about this function's arithmetic.
    So the segment lives here and the four callers ask.
    """
    return records_root / "projects" / project


def today_path(records_root: Path, project: str) -> Path:
    """Where a person's selection for one project lives."""
    return project_dir(records_root, project) / "board" / "today.yaml"


def _rehang(locator: str, workspace_root: Path) -> tuple[Path, str] | None:
    """An absolute locator pointing at another node, re-hung under this one.

    `/opt/applications/data/home-hwz/workspace/records/projects/litellm/items/
    LIT-0203/card.yaml` names a layout, not a machine: the authority wrote it on
    Beijing, and the workspace under it was migrated rather than copied. The
    tail from `workspace/` — or from `records/`, for a locator that stops
    naming the root — is the part that still means something here, so it is
    joined to THIS node's workspace root. The foreign root is returned beside
    the path so the row can report what it substituted rather than presenting
    the result as the locator's own answer.
    """
    parts = Path(locator).parts
    for marker in ("workspace", "records"):
        for index in range(len(parts) - 2, 0, -1):
            if parts[index] != marker:
                continue
            tail = parts[index + 1:] if marker == "workspace" else parts[index:]
            if tail:
                return (workspace_root.joinpath(*tail),
                        str(Path(*parts[:index])))
    return None


def resolve_card_path(*, locator, project: str, item_id: str,
                      records_root: Path, workspace_root: Path) -> dict:
    """Where this Item's card is on THIS node, and which rule decided it.

    `card_locator` is written by the authority — whatever node composed the
    projection — and three forms have been seen on this host:

      * repository-relative (`records/projects/<p>/items/<ID>/card.yaml`),
        resolved against the WORKSPACE root and never against the cwd;
      * absolute and present, used as written;
      * absolute and naming ANOTHER node's root, which is the interesting one.
        The path is a claim about a machine, and this node's workspace is at a
        different place; the declaration outlived its host. Its tail is re-hung
        under this node's workspace root and the row says so.

    The re-hang is reported and never silent: `how` is `DECLARED`, `REBASED`,
    `CONVENTIONAL` or `UNRESOLVED`, `locator` carries the original string
    verbatim, and `why` states the substitution. A locator that resolves under
    neither root is `UNRESOLVED`, which is a statement about the POINTER, not
    about the Item — and it is kept apart from "this Item has no card", which
    is what a reader would otherwise conclude. Composing
    `records/<project>/items/<ID>/card.yaml` from parts, as a first version
    did, put all 17 of this node's real cards in that wrong bucket by dropping
    the `projects/` segment.
    """
    conventional = (project_dir(records_root, project) / "items" / item_id
                    / ENTRY_DOCUMENT)
    if not locator:
        return {"path": conventional, "item_dir": conventional.parent,
                "how": "CONVENTIONAL", "locator": None,
                "why": ("the projection carries no `card_locator`, so the "
                        "conventional records path is used and nothing is "
                        "claimed about whether the authority meant a different "
                        "one")}
    raw = str(locator)
    candidate = Path(raw)
    if candidate.is_absolute():
        if candidate.is_file():
            return {"path": candidate, "item_dir": candidate.parent,
                    "how": "DECLARED", "locator": raw, "why": ""}
        foreign = _rehang(raw, workspace_root)
        if foreign is not None and foreign[0].is_file():
            return {"path": foreign[0], "item_dir": foreign[0].parent,
                    "how": "REBASED", "locator": raw,
                    "why": (f"the locator is absolute and absent on this node; "
                            f"its tail resolves to {foreign[0]}. The locator "
                            f"was written where the workspace root was "
                            f"{foreign[1]!r}, and that declaration outlived "
                            f"the machine it described")}
        return {"path": candidate, "item_dir": conventional.parent,
                "how": "UNRESOLVED", "locator": raw,
                "why": (f"the locator is absolute and resolves to nothing on "
                        f"this node, and nothing under {workspace_root} matches "
                        f"its tail either")}
    joined = workspace_root / raw
    if joined.is_file():
        return {"path": joined, "item_dir": joined.parent,
                "how": "DECLARED", "locator": raw, "why": ""}
    if conventional.is_file():
        return {"path": conventional, "item_dir": conventional.parent,
                "how": "CONVENTIONAL", "locator": raw,
                "why": (f"the locator {raw!r} resolves to nothing under "
                        f"{workspace_root}; the conventional records path is "
                        f"used instead")}
    return {"path": joined, "item_dir": joined.parent,
            "how": "UNRESOLVED", "locator": raw,
            "why": (f"the locator {raw!r} resolves to nothing under "
                    f"{workspace_root}")}


def load_card(resolution: dict, item_id: str, *, item_dir: Path | None = None,
              rule: dict | None = None, tree=None) -> dict:
    """One Item's card, with the ways it can fail kept apart.

    An absent card is `MISSING` whether the cause is that the file is not there
    or that the locator points nowhere; `how` and `why` carry which, because the
    two call for different actions — one is a document to write and the other is
    a pointer to fix. An unreadable card is a third state and is never reported
    as absence.

    `rule` and `tree` are how a lagging checkout gets the card's CONTENT and not
    only the news that the ref has it. Without them this reader answered about
    one surface while the `● card.yaml` row two lines below answered about
    another, and the header said `no card, so nothing declared one` about a card
    the same block had just reported PRESENT. The content is read through the
    same `_authority_bytes` that row uses, so the two cannot drift; `read_from`
    on the result names which surface answered, and it is set on every outcome,
    including the unreadable one.
    """
    path = resolution["path"]
    base = {"path": str(path), "how": resolution["how"],
            "locator": resolution["locator"], "resolution_why": resolution["why"],
            "read_from": "worktree"}
    document, why = _read_yaml(path)
    if document is None and why == "not present" and tree is not None and rule is not None:
        found = _authority_bytes(item_dir, rule, tree)
        if found is not None and found[3] is not None:
            base = {**base, "read_from": tree.upstream_ref,
                    "read_from_revision": tree.upstream_revision,
                    "authority_path": found[0]}
            document, why = _parse_yaml_text(found[3])
    if document is None:
        if why != "not present":
            return {**base, "state": UNREADABLE, "why": why, "document": None}
        if resolution["why"]:
            return {**base, "state": MISSING, "document": None,
                    "why": (f"{resolution['why']}; and no card is there under "
                            f"that rule either, so the card is absent and not "
                            f"merely unreachable")}
        return {**base, "state": MISSING, "why": why, "document": None}
    declared = document.get("id")
    if declared and declared != item_id:
        # The same refusal `registration.Candidate.from_dict` makes, for the
        # same reason: a document whose id is not where it lives is a question
        # about a different thing, and joining it would put one Item's title on
        # another Item's state.
        return {**base, "state": UNREADABLE, "document": None,
                "why": (f"declares id {declared!r} and is stored under "
                        f"{item_id!r}")}
    return {**base, "state": PRESENT, "why": "", "document": document}


def _priority(card: dict | None) -> dict:
    """`{source, personal}` — a level, or UNPRODUCED, or UNREADABLE.

    Nothing is inferred. A card that declares `subject: P1` in a title does not
    have a priority, which is the same distinction `global_board.py` draws when
    it reports `titles_naming_a_level` separately from the buckets.
    """
    if card is None:
        out = {"state": UNPRODUCED, "why": "no card, so nothing declared one"}
        return {"source": dict(out), "personal": dict(out)}
    levels = {}
    for field, label in (("source_priority", "source"), ("personal_priority", "personal")):
        value = card.get(field)
        if value is None:
            levels[label] = {
                "state": UNPRODUCED,
                "why": (f"the card declares no `{field}`. The field is proposed "
                        f"in `intake/workchain-contract.yaml#card_extension` and "
                        f"is not approved, so nothing here invents one"),
            }
        elif str(value) in PRIORITY_LEVELS:
            levels[label] = {"state": str(value), "why": ""}
        else:
            levels[label] = {
                "state": UNREADABLE,
                "why": (f"`{field}: {value!r}` is not one of "
                        f"{', '.join(PRIORITY_LEVELS)}. A level outside the "
                        f"vocabulary is reported rather than coerced to the "
                        f"nearest one"),
            }
    return levels


def _delivery(document: dict | None, registered) -> dict:
    """The delivery surface: what the card SAYS, against what the REGISTER holds.

    The work entry printed `制品 18` and nothing else. That number answers a
    question about the register — how many rows were written into it — and it
    was read as an answer about the delivery. On 2026-10-08 `SRV-0011` showed
    18 against a card declaring eight deliverable kinds, none of which was
    among them; the same shape held for 19 of the 20 Items that declare any.
    A fact about the reader reported as a fact about the world is this module's
    recurring defect, so the two faces are computed here and kept apart.

    `compared_by` is not decoration. The comparison is the literal kind string,
    and a reader who cannot tell «this name is absent from the register» from
    «this artifact was never produced» will read the first as the second — the
    register's vocabulary and the cards' are not one vocabulary yet, and saying
    so is the honest half of the answer.

    `registered` is `None` when the register was never looked in — the register
    is read off the Item's row in a projection, so an Item no projection carries
    has no register to compare against. `0` is a count and `None` is the absence
    of one: reporting `登记 0 条` there would be this module's recurring defect
    again, a fact about what the reader consulted reported as a fact about the
    delivery.
    """
    if document is None:
        declared, state = [], UNREADABLE
        why = "no card was read, so nothing here declared a delivery surface"
    else:
        declared = [str(d) for d in (document.get("expected_deliverables") or [])]
        state = DECLARED if declared else NOT_DECLARED
        why = ("" if declared else
               "the card declares no `expected_deliverables`. This is not the "
               "same fact as an unmet delivery: no surface was stated, so "
               "nothing can be compared against one")
    if registered is None:
        return {
            "declared": {"state": state, "kinds": declared, "why": why},
            "registered": {
                "count": None, "by_kind": {}, "kinds": [],
                "why": ("the register was not consulted: it is read from this "
                        "Item's row in a projection, and no projection on this "
                        "node carries one, so neither the count nor which kinds "
                        "it holds is known here"),
            },
            # `None` and not `[]`: an empty list is the answer "nothing
            # matched", and this is the answer "nothing was compared".
            "matched": None, "absent": None, "undeclared": None,
            "compared_by": "exact kind string",
        }
    by_kind: dict[str, int] = {}
    for row in registered:
        kind = str((row or {}).get("kind") or "")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        "declared": {"state": state, "kinds": declared, "why": why},
        "registered": {"count": len(list(registered)), "by_kind": by_kind,
                       "kinds": sorted(by_kind)},
        "matched": [d for d in declared if d in by_kind],
        "absent": [d for d in declared if d not in by_kind],
        "undeclared": sorted(k for k in by_kind if k not in declared),
        "compared_by": "exact kind string",
    }


def _documents(item_dir: Path, state: str | None, rules: list[dict],
               skeletons: dict | None = None, tree: "TreePosition | None" = None,
               ) -> list[dict]:
    """Every document, with PRESENT / MISSING / NOT_YET_REQUIRED / UNREADABLE.

    `state` is the Item's own state, read from the projection. The two-word
    answer matters more than the list: a board that showed only the files that
    exist would be unable to say whether the rest were missing or had not been
    asked for. `state` is `None` for an Item the control plane does not carry,
    and then the answer for an absent document is the fourth word rather than
    either of those two — see `REQUIREMENT_UNKNOWN`.

    A document that resolves is reported with how it resolved and how much is
    there, so `design/` holding three research notes and `design.md` holding one
    are told apart without this module reading either one.

    `tree` is optional and changes nothing when it is absent, which keeps every
    existing caller and fixture on exactly the answer it had. When it is given,
    a document that resolves under no rule is asked of the ref the tree
    follows, and when that tree is behind it and never carried the path, the
    document is READ from there: the row becomes PRESENT with `read_from`
    naming the surface that answered. Reporting only the cause of an absence
    was the half-fix — the Item is waiting on a brief its consumers still could
    not open. A document nothing could be read from keeps MISSING and gets the
    cause attributed, which is the answer for the negative control.
    """
    out = []
    for rule in rules:
        name = rule["name"]
        path, how, count = _resolve_document(item_dir, rule)
        required = None if state is None else state in rule["required_at"]
        answer = {"document": name, "state": None, "required": required,
                  "resolved_by": how, "count": count,
                  "path": str(path if path is not None else item_dir / name),
                  "why": ""}
        if path is not None:
            answer["state"] = PRESENT
            # Which surface answered, on every row and not only the unusual
            # ones: a consumer that has to infer it from the presence of some
            # other field will infer it wrongly exactly once and keep the bug.
            answer["read_from"] = "worktree"
            # What is IN it, when there is one thing to read. A `●` beside a
            # one-line file and a `●` beside a finished brief looked identical
            # before this, which made the board's mark a fact about a filename.
            answer["skeleton"] = check_skeleton(
                read_document_text(path, how), skeleton_of(skeletons, name))
            out.append(answer)
            continue
        if state is None:
            # Placed BEFORE the card's own branch on purpose: that branch's
            # sentence is "the Item is on the board and has no card", and the
            # whole reason this state exists is that the Item is on no board.
            # Saying it here would be this module's recurring defect — a fact
            # about the projection reported as a fact about the Item.
            answer |= {"state": REQUIREMENT_UNKNOWN,
                       "why": ("whether this Item owes it depends on its state, "
                               "and no projection on this node states one. What "
                               "is answered is that the file is not here")}
            out.append(answer)
            continue
        if name == ENTRY_DOCUMENT and not required:
            # The card is what put this Item on the board at all, so a card
            # that is absent is never "not required yet" — it is the one
            # document whose absence is a defect at every state.
            answer |= {"state": MISSING, "required": True,
                       "why": ("the Item is on the board and has no card; §6 "
                               "lets a candidate enter on a card alone, so a "
                               "candidate with no card has nothing")}
        elif required:
            if tree is not None:
                answer |= _read_from_the_authority(item_dir, rule, tree,
                                                   skeletons, name)
            if answer["state"] is None:
                answer |= {"state": MISSING,
                           "why": (f"an Item in {state} is expected to have it, and "
                                   f"it resolves under none of "
                                   f"{list(rule['resolved_by'])}")}
                if tree is not None:
                    _explain_absence_by_tree(answer, item_dir, rule, tree)
        else:
            answer |= {"state": NOT_YET_REQUIRED,
                       "why": f"an Item in {state} is not expected to have it yet"}
        out.append(answer)
    return out


# ---------------------------------------------------------------------------
# The completion boundary: what an Item still owes
# ---------------------------------------------------------------------------

def _completion_rules(contract: dict) -> dict:
    """The completion boundary from the contract, checked, or a refusal.

    `requires` stays a sentence for a person. The two lists beside it are the
    same rule in the form this module can be wrong about, and a test asserts the
    sentence names exactly what they name — a second spelling is defensible only
    while something fails when the two drift apart.

    Three refusals here, and each is a way the block could read as satisfied
    without ever having been checked:

      * no state, or a state that `assessed_at` never tests. A claim that is
        never tested is the defect this whole section exists for.
      * a state listed as both completion and not.
      * nothing to check, or a required document `item_documents` does not
        declare — a requirement against a document nobody declares can neither
        be satisfied nor violated, so it would report one gap forever.
    """
    block = contract.get("completion")
    if not isinstance(block, dict):
        raise Refused(
            "`completion` is absent or is not a mapping, so nothing states what "
            "an Item owes and every Item would read as finished.")
    states = tuple(block.get("states") or ())
    assessed = tuple(block.get("assessed_at") or ())
    fields = tuple(block.get("requires_fields") or ())
    names = tuple(block.get("requires_documents") or ())
    if not states or not assessed:
        raise Refused(
            "`completion` names no state, or names one without saying where the "
            "claim is tested. An empty requirement is not a lenient rule, it is "
            "the absence of one.")
    untested = sorted(set(states) - set(assessed))
    if untested:
        raise Refused(
            f"`completion.states` claims completion at {untested} and "
            f"`assessed_at` never tests there, so the claim could not be "
            f"checked — which is a declaration, not a boundary.")
    both = sorted(set(states) & set(block.get("not_completion") or ()))
    if both:
        raise Refused(
            f"{both} is listed as both completion and not completion. One of "
            f"the two is wrong, and code that picked either would be picking.")
    if not fields and not names:
        raise Refused(
            "`completion` names nothing to check, which would make every Item "
            "in an assessed state complete on the strength of its state alone.")
    declared = {rule["name"] for rule in _document_rules(contract)}
    unknown = [name for name in names if name not in declared]
    if unknown:
        raise Refused(
            f"`completion.requires_documents` names {unknown}, which "
            f"`item_documents` does not declare. A requirement against a "
            f"document nobody declares can neither be satisfied nor violated.")
    return {"states": frozenset(states), "assessed_at": frozenset(assessed),
            "fields": fields, "documents": names,
            "prose": str(block.get("requires") or "")}


def _set(value) -> bool:
    """Is this field set?

    `None`, an empty string and an empty collection are absences wearing a
    value's clothes, and a requirement checked with `is not None` would read
    `outcome: ""` as an outcome having been recorded.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def completion_of(row: dict, rules: dict) -> dict:
    """What one Item still owes, and whether the question could be asked.

    Read from the row alone: the contract names the requirements and the row
    carries the evidence. Four answers, and keeping the first and the fourth
    apart is most of why this function exists:

        NOT_ASSESSED  the contract does not test completion at this state, so
                      nothing is owed. **Not** an evaluation that passed.
        COMPLETE      the state claims completion and everything required is
                      present.
        DISCHARGED    the state does not claim completion and nothing declared is
                      missing — an Item waiting on a person, not on work.
        OPEN          something the contract requires is absent, and the gap is
                      named, with absent and not-reported kept apart.
    """
    state = row.get("state")
    if state not in rules["assessed_at"]:
        return {"state": NOT_ASSESSED, "assessed": False, "missing_fields": [],
                "missing_documents": [], "unreported_documents": [],
                "why": (f"the contract tests completion at "
                        f"{sorted(rules['assessed_at'])}, and this Item is in "
                        f"{state}")}
    documents = row.get("documents")
    if not isinstance(documents, list):
        return {"state": NOT_ASSESSED, "assessed": False, "missing_fields": [],
                "missing_documents": [], "unreported_documents": [],
                "why": ("this row carries no `documents` list, so which required "
                        "documents resolve could not be read")}
    by_name = {d.get("document"): d for d in documents if isinstance(d, dict)}
    missing_fields = [f for f in rules["fields"] if not _set(row.get(f))]
    missing_documents, unreported = [], []
    for name in rules["documents"]:
        entry = by_name.get(name)
        if entry is None:
            # Not the same as absent: the row does not mention it. It is counted
            # as owed either way — the requirement is declared and the row does
            # not show it met — but a reader is told which of the two it is.
            unreported.append(name)
        elif entry.get("state") == NOT_YET_REQUIRED:
            raise Refused(
                f"`completion` is tested at {state} and names `{name}`, while "
                f"`item_documents` does not require `{name}` at {state}. The "
                f"test could never fail, so it is not a test.")
        elif entry.get("state") != PRESENT:
            missing_documents.append(name)
    if missing_fields or missing_documents or unreported:
        return {"state": OPEN, "assessed": True, "missing_fields": missing_fields,
                "missing_documents": missing_documents,
                "unreported_documents": unreported,
                "why": (f"in {state} the contract requires "
                        f"{list(rules['fields']) + list(rules['documents'])}, and "
                        f"{sorted(missing_fields) + sorted(missing_documents) + sorted(unreported)} "
                        f"is not there")}
    claimed = state in rules["states"]
    return {"state": COMPLETE if claimed else DISCHARGED, "assessed": True,
            "missing_fields": [], "missing_documents": [],
            "unreported_documents": [],
            "why": (f"every requirement the contract names at {state} is present"
                    + ("" if claimed else
                       f"; {state} is not completion, so what is owed here is a "
                       f"person's acceptance and not work"))}


# ---------------------------------------------------------------------------
# Composing
# ---------------------------------------------------------------------------

def _rows_of(projects: list[dict], *, records_root: Path, workspace_root: Path,
             documents: list[dict], completion: dict,
             skeletons: dict | None = None,
             tree: "TreePosition | None" = None) -> list[dict]:
    rows = []
    for project in projects:
        if project["state"] != "READ":
            continue
        for item in project["document"].get("items") or []:
            if not isinstance(item, dict) or not item.get("id"):
                raise Refused(
                    f"a row in {project['path']} has no id. The projection is "
                    f"the authority on Item state and a row it cannot name is a "
                    f"row nobody can act on.")
            item_id = str(item["id"])
            resolution = resolve_card_path(
                locator=item.get("card_locator"), project=project["project"],
                item_id=item_id, records_root=records_root,
                workspace_root=workspace_root)
            # The sibling documents are looked for BESIDE THE CARD, wherever the
            # card turned out to be, and not under a path recomposed here. A
            # brief next to a card is one Item's brief; the same filename under
            # a directory this module guessed is a different document.
            item_dir = resolution["item_dir"]
            card = load_card(
                resolution, item_id, item_dir=item_dir,
                rule=next((r for r in documents
                           if r.get("name") == ENTRY_DOCUMENT), None),
                tree=tree)
            row = {
                "id": item_id,
                "project": project["project"],
                "kind": item.get("kind"),
                "title": item.get("title"),
                "state": str(item.get("state") or UNKNOWN),
                "outcome": item.get("outcome"),
                "card_locator": item.get("card_locator"),
                "card": {"state": card["state"], "why": card["why"],
                         "path": card["path"], "how": card["how"],
                         "read_from": card.get("read_from"),
                         "read_from_revision": card.get("read_from_revision"),
                         "resolution_why": card["resolution_why"]},
                "item_dir": str(item_dir),
                "priority": _priority(card["document"]),
                "superseded_by": ((card["document"] or {}).get("superseded_by")
                                  if card["state"] == PRESENT else None),
                "documents": _documents(item_dir, str(item.get("state") or ""),
                                        documents, skeletons, tree),
                "runs": sum(len(a.get("runs") or ())
                            for a in item.get("activities") or ()),
                "deliverables": len(item.get("deliverables") or ()),
                "delivery": _delivery(card["document"],
                                      item.get("deliverables") or ()),
                "ready_activities": [{"id": a.get("id"), "kind": a.get("kind")}
                                     for a in item.get("activities", []) if a.get("state") == "READY"],
            }
            # Computed HERE, where the contract and the documents are both in
            # hand, and then carried on the row. Every reader downstream — the
            # board, the focus source, the daily cycle — gets the same answer
            # instead of each re-deriving it from fields it happens to have.
            row["completion"] = completion_of(row, completion)
            rows.append(row)
    return rows


def load_today(path: Path) -> dict:
    """The authored selection, or an explicit statement that there is none.

    An absent `today.yaml` is not "nothing is important today". It is "nobody
    has chosen", and the board says which. The distinction is the one this
    module exists to keep: a person reading an empty 今日重点 has to be able to
    tell "I have not chosen yet" from "I chose nothing".
    """
    if not path.is_file():
        return {"state": UNPRODUCED, "items": [], "why":
                f"no selection was recorded at {path}", "path": str(path),
                "date": None}
    document, why = _read_yaml(path)
    if document is None:
        return {"state": UNREADABLE, "items": [], "why": why, "path": str(path),
                "date": None}
    items = [str(x) for x in (document.get("items") or [])]
    if not items:
        return {"state": "EMPTY", "items": [], "path": str(path),
                "date": document.get("date"),
                "why": ("a selection was recorded and it names no Item — a "
                        "person chose nothing, which is not the same as not "
                        "having chosen")}
    return {"state": "SELECTED", "items": items, "path": str(path),
            "date": document.get("date"), "why": ""}


def write_today(*, records_root: Path, project: str, items: list[str],
                date: str, note: str = "", actor: str = "") -> dict:
    """Record a person's selection. The module owns the document's shape.

    Here rather than in the CLI for the reason the whole tree is arranged this
    way: a document written by one function and read by another is a shape
    nobody checks, and the two live in different files. `load_today` above is
    the reader; this is the writer; they are four hundred lines apart on
    purpose and both are tested against each other.
    """
    target = today_path(records_root, project)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {"schema": "serveros/today.v1", "project": project,
                "date": date, "items": list(items), "note": note,
                "authored_by": actor}
    target.write_text(yaml.safe_dump(document, allow_unicode=True,
                                     sort_keys=False), encoding="utf-8")
    return {"path": str(target), "document": document}


def load_sources(*, records_root: Path, project: str) -> dict:
    """The authored source list, or an explicit statement that there is none.

    `sources.yaml` is where §7's 关注源清单 lives. An absent file is
    `UNPRODUCED` and not an empty list, and an unreadable one is neither — a
    list that failed to parse is not a project that follows nothing.
    """
    path = project_dir(records_root, project) / "context" / "sources.yaml"
    if not path.is_file():
        return {"state": UNPRODUCED, "sources": [], "path": str(path),
                "why": (f"no source list was authored at {path}. The three "
                        f"feishu surfaces are still mapped; what is missing is "
                        f"the list of what a person follows, which is a "
                        f"decision and not a measurement")}
    document, why = _read_yaml(path)
    if document is None:
        return {"state": UNREADABLE, "sources": [], "path": str(path), "why": why}
    sources = document.get("sources")
    if not isinstance(sources, list):
        return {"state": UNREADABLE, "sources": [], "path": str(path),
                "why": ("the file parses and carries no `sources` list. An "
                        "empty document is not a project that follows nothing, "
                        "it is a document that declares nothing")}
    return {"state": "READ", "sources": sources, "path": str(path), "why": ""}


def _select(rows: list[dict], spec: dict, today: dict) -> list[dict]:
    """One section's rows, from the section's own selector.

    No fallback bucket. An Item whose state this contract does not name is
    counted in `unplaced` and printed with its state, so a new work-engine
    state shows up as an unplaced Item rather than being quietly filed under
    待排期 — where it would read as scheduled work.
    """
    chosen = []
    for row in rows:
        if "today" in spec:
            if bool(spec["today"]) != (row["id"] in today["items"]):
                continue
        if "priority" in spec:
            level = row["priority"]["personal"]
            if level["state"] != spec["priority"]:
                continue
        if "state" in spec and row["state"] not in spec["state"]:
            continue
        chosen.append(row)
    return chosen


def compose_board(*, contract: dict, projects: list[dict], records_root: Path,
                  workspace_root: Path, today_by_project: dict | None = None,
                  skeletons: dict | None = None,
                  tree: "TreePosition | None" = None) -> dict:
    """The personal board: §6's sections, in §6's order, top to bottom.

    `tree` is the same optional position `compose_item` takes, and it is here
    for the reason the two readers must not disagree: the board counts what is
    present and composes each row's completion boundary from that count, so a
    board read without the position would report a document MISSING in the
    same breath as `work item` reporting it PRESENT — two answers to one
    question, both looking authoritative.
    """
    documents = _document_rules(contract)
    rows = _rows_of(projects, records_root=records_root,
                    workspace_root=workspace_root, documents=documents,
                    completion=_completion_rules(contract), skeletons=skeletons,
                    tree=tree)
    today_by_project = today_by_project or {}
    section_specs = (contract.get("personal_board") or {}).get("sections") or []
    if not section_specs:
        raise Refused("`personal_board.sections` is empty, so the board has no "
                      "order — and an unordered board is not a board")

    # One selection per project. An Item selected in a project whose selection
    # could not be read is NOT today's: an unreadable file is not a selection,
    # and guessing which Items it holds would be inventing a person's decision.
    merged_today = {"state": UNPRODUCED, "items": [], "why": "", "by_project": {}}
    states = set()
    for project, today in today_by_project.items():
        merged_today["by_project"][project] = today
        states.add(today["state"])
        merged_today["items"].extend(today["items"])
    if states == {"SELECTED"}:
        merged_today["state"] = "SELECTED"
    elif states:
        merged_today["state"] = "PARTIAL" if "SELECTED" in states else sorted(states)[0]
        merged_today["why"] = ("；".join(
            f"{p}: {t['state']}" for p, t in sorted(today_by_project.items())
            if t["state"] != "SELECTED"))

    sections = []
    for spec in section_specs:
        sections.append({
            "id": spec.get("id"), "title": spec.get("title"),
            "struck_through": bool(spec.get("struck_through")),
            # A section that had to be added to §6's list carries its reason
            # with it, so a reader of the rendered board can see which sections
            # §6 names and which one this contract added rather than being
            # given a list that looks uniformly authoritative.
            "because": spec.get("because"),
            "rows": _select(rows, {k: v for k, v in spec.get("selects", {}).items()},
                            merged_today),
        })

    placed = {row["id"] for section in sections for row in section["rows"]}
    unplaced = [{"id": r["id"], "project": r["project"], "state": r["state"],
                 "why": (f"state {r['state']} is named by no section of "
                         f"`personal_board.sections`")}
                for r in rows if r["id"] not in placed]
    # A row that a priority section skipped for being today's is placed, in the
    # today section. Only rows no section claimed are unplaced, which is why
    # the check is membership rather than "did any selector match".

    return {
        "schema": SCHEMA,
        "at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "today": merged_today,
        "sections": sections,
        "unplaced": unplaced,
        # `taken` travels with each project because `at` above is when this
        # board was composed, not when the state on it was measured. Every row
        # in every section came from one of these documents, and on a node
        # where the control plane that writes them does not run, this field is
        # the only place that age is visible. Copied verbatim: this module says
        # when the projection was taken, not whether that is recent.
        "projects": [{"project": p["project"], "state": p["state"],
                      "why": p["why"], "path": p["path"],
                      "taken": (p.get("document") or {}).get("generated_at"),
                      # The names this one projection answered for, when the
                      # caller named an alias. Carried rather than swallowed:
                      # a caller who asked for three projects and got two rows
                      # must be able to see that the third was a second name for
                      # one of them, not a third project that went missing.
                      "also_named": list(p.get("also_named") or [])}
                     for p in projects],
        "counts": {
            "items": len(rows),
            "projects_read": sum(1 for p in projects if p["state"] == "READ"),
            "projects_unread": sum(1 for p in projects if p["state"] != "READ"),
            "cards_missing": sum(1 for r in rows if r["card"]["state"] == MISSING),
            "cards_unreadable": sum(1 for r in rows if r["card"]["state"] == UNREADABLE),
            # A card that resolved under a rule other than the locator's own.
            # Counted apart because a rebased card is readable and correct while
            # the locator beside it is still wrong, and a reader who saw only
            # "0 missing" would have no reason to go and fix the pointer.
            # EMPTY only. A document whose shape diverges from the PROPOSED
            # headings is not a gap, and counting it here is how a number
            # becomes a false alarm on every row of the board.
            "documents_empty": sum(
                1 for r in rows for d in r["documents"]
                if (d.get("skeleton") or {}).get("state") == "EMPTY"),
            "documents_diverging": sum(
                1 for r in rows for d in r["documents"]
                if (d.get("skeleton") or {}).get("state") == "DIVERGES"),
            "cards_rebased": sum(1 for r in rows
                                 if r["card"]["how"] == "REBASED"),
            "cards_unresolved": sum(1 for r in rows
                                    if r["card"]["how"] == "UNRESOLVED"),
            "documents_missing": sum(1 for r in rows
                                     for d in r["documents"] if d["state"] == MISSING),
            "priorities_unproduced": sum(
                1 for r in rows
                for side in ("source", "personal")
                if r["priority"][side]["state"] == UNPRODUCED),
        },
    }


def _row_from_the_records(*, project: str, item_id: str, documents: list[dict],
                          completion: dict, records_root: Path,
                          workspace_root: Path,
                          skeletons: dict | None = None,
                          tree: "TreePosition | None" = None) -> dict | None:
    """One Item's row from the records tree alone, when no projection names it.

    THE DEFECT THIS EXISTS FOR
    --------------------------
    `serverosctl work item LIT-0106` answered `未找到 —— LIT-0106 is in no
    projection under …`. The card was on this node's disk the whole time, and
    `未找到` is a fact about the BOARD PROJECTION reported as a fact about the
    Item — the shape of `制品 18` and of `卡片 UNRESOLVED`, one surface over.
    Measured on this node, 2026-10-08: TWELVE Items carry a card under
    `records/projects/<project>/items/` and no row in that project's projection
    — `LIT-0106`, `0107`, `0215`, `0216`, `0217`, `0218`, `0219`, and
    `SRV-0001`, `0002`, `0003`, `0005`, `0008`. It is also why `LIT-0220`'s first
    acceptance criterion — ruling on its relation to `LIT-0106` — could not be
    acted on by anyone reading through the formal entry.

    WHAT IT DOES NOT DO
    -------------------
    It reads no control-plane column off the disk and guesses none of them. The
    projection owns `state`, `runs` and `deliverables`; they come back `UNKNOWN`
    here, and `_delivery` reports that the register was not consulted rather
    than counting it as empty. Which documents the Item OWES depends on that
    same state, so an absent document is `REQUIREMENT_UNKNOWN` and not `MISSING`.

    Nothing here is fed to `_rows_of`, so the board and the focus source keep
    reading exactly the rows their projections hold. A consumer that needs a
    projection's answer must ask for one: `compose_item` marks the payload with
    `in_projection`, and `mcp/continuation_mcp._completion_reader` requires it,
    because a boundary composed for an Item no projection carries would be this
    adapter's blindness wearing the Item's name.

    The CARD may be read from the authority ref when the tree is behind and
    never carried it, through the same `load_card`/`_authority_bytes` the
    projection path uses. The sibling documents may not: their requirement is
    unknowable here, so borrowing bytes to answer "present" would answer a
    question nobody asked while the question that matters stays open.

    `None` means no card resolves on the conventional path either, which keeps
    `found: False` for an Item that is in neither place on this node.
    """
    resolution = resolve_card_path(locator=None, project=project,
                                   item_id=item_id, records_root=records_root,
                                   workspace_root=workspace_root)
    # `resolve_card_path`'s own wording for a locator-less resolution is "the
    # projection carries no `card_locator`" — true on the path that HAS a
    # projection row and false here, where the reason is that there is no row at
    # all. A fact about a row that does not exist is the defect this whole
    # function is about, so the sentence is replaced rather than reused.
    resolution = {**resolution, "why": (
        "no projection on this node carries a row for this Item, so no card "
        "locator was declared either; the conventional records path is used "
        "and nothing is claimed about whether an authority meant a different "
        "one")}
    item_dir = resolution["item_dir"]
    card = load_card(
        resolution, item_id, item_dir=item_dir,
        rule=next((r for r in documents if r.get("name") == ENTRY_DOCUMENT),
                  None),
        tree=tree)
    if card["state"] != PRESENT:
        return None
    document = card["document"] or {}
    row = {
        "id": item_id,
        "project": project,
        "kind": document.get("kind"),
        "title": document.get("title"),
        "state": UNKNOWN,
        "outcome": None,
        "card_locator": None,
        "card": {"state": card["state"], "why": card["why"],
                 "path": card["path"], "how": card["how"],
                 "read_from": card.get("read_from"),
                 "read_from_revision": card.get("read_from_revision"),
                 "resolution_why": card["resolution_why"]},
        "item_dir": str(item_dir),
        "priority": _priority(card["document"]),
        "superseded_by": document.get("superseded_by"),
        "documents": _documents(item_dir, None, documents, skeletons, tree),
        "runs": UNKNOWN,
        "deliverables": UNKNOWN,
        "delivery": _delivery(card["document"], None),
        "ready_activities": [],
    }
    row["completion"] = completion_of(row, completion)
    return row


def compose_item(*, contract: dict, records_root: Path, workspace_root: Path,
                 project: str, item_id: str, projects: list[dict],
                 skeletons: dict | None = None,
                 tree: "TreePosition | None" = None) -> dict:
    """One Item's chain: what it is, and which adjacent document resolves.

    The documents are listed in the contract's order, which is the chain's
    order — card, brief, design, plan, report — so a reader sees where the
    chain stops. `undecided` is empty when every required document is present
    and names each one that is not, with the state that made it required.

    `tree`, when the caller supplies one, is carried on the payload as
    `position` as well as being used to attribute an absence. A reader that has
    to open a second tool to learn which generation the answer came from will
    not, and the answer is only meaningful with it.

    The projection is asked first and the RECORDS TREE second, and which one
    answered is on the payload as `in_projection`. `found` answers whether this
    node can answer for the Item at all; it never answered whether a projection
    carries it, and reading it as that is what made `未找到` a fact about the
    Item.
    """
    documents = _document_rules(contract)
    completion = _completion_rules(contract)
    row = None
    for candidate in _rows_of(projects, records_root=records_root,
                              workspace_root=workspace_root,
                              documents=documents, completion=completion,
                              skeletons=skeletons, tree=tree):
        if candidate["project"] == project and candidate["id"] == item_id:
            row = candidate
            break
    listed = row is not None
    if row is None:
        row = _row_from_the_records(
            project=project, item_id=item_id, documents=documents,
            completion=completion, records_root=records_root,
            workspace_root=workspace_root, skeletons=skeletons, tree=tree)
    if row is None:
        # Neither place holds it. The message names BOTH, because a reader told
        # only about the projection will go looking for a projection to fix
        # when the card may be the thing that is missing — and the two call for
        # different actions. A projection that does not carry the Item may also
        # mean the Item is in another project.
        conventional = (project_dir(records_root, project) / "items" / item_id
                        / ENTRY_DOCUMENT)
        return {"schema": "serveros/item-chain.v1", "found": False,
                "project": project, "item": item_id,
                "why": (f"{item_id} is in no projection under "
                        f"{', '.join(p['path'] for p in projects) or 'no board root'}, "
                        f"and no card for it is under {conventional} either. "
                        f"Either it belongs to another project, or no "
                        f"projection for this project reached this host, or it "
                        f"has no card yet."),
                "records": {"path": str(conventional), "state": MISSING},
                "projects": [{"project": p["project"], "state": p["state"],
                              "why": p["why"]} for p in projects]}
    undecided = [{"document": d["document"], "state": d["state"],
                  "because": d["why"]}
                 for d in row["documents"] if d["state"] == MISSING]
    return {
        "schema": "serveros/item-chain.v1", "found": True,
        "project": project, "item": item_id,
        "in_projection": {
            "listed": listed,
            "why": ("" if listed else
                    (f"no projection on this node carries a row for {item_id} "
                     f"— the board roots read were "
                     f"{', '.join(p['path'] for p in projects) or 'none'}. The "
                     f"chain below is composed from the records tree at "
                     f"{row['item_dir']}, so it answers for the Item's "
                     f"documents and does NOT know the Item's state, Run count "
                     f"or deliverable count; those are the projection's "
                     f"columns and no projection here holds them")),
        },
        "title": row["title"], "kind": row["kind"], "state": row["state"],
        "outcome": row["outcome"], "superseded_by": row["superseded_by"],
        "priority": row["priority"], "card": row["card"],
        "item_dir": row["item_dir"],
        "documents": row["documents"],
        "completion": row["completion"],
        "runs": row["runs"], "deliverables": row["deliverables"],
        "delivery": row["delivery"],
        "undecided": undecided,
        "position": None if tree is None else {
            "state": tree.state, "root": str(tree.root) if tree.root else None,
            "revision": tree.revision, "branch": tree.branch,
            "upstream_ref": tree.upstream_ref,
            "upstream_revision": tree.upstream_revision,
            "ahead": tree.ahead, "behind": tree.behind, "why": tree.why},
    }


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def disk_coverage(*, paths, role_names=(), refs=()) -> dict:
    """The disk map, from the root contract, with each name's landing place.

    Three vocabularies, kept apart because `runtime/paths.py` says they come
    apart: a ROOT is where something is, a SEMANTIC ROLE is what something is,
    and — the one that matters here — the role is the stable name while the
    path is the fact that moves. Reporting them in one list is what makes a
    move visible as a change of path rather than as a name that stopped
    resolving, and the `kind` column is what keeps a reader from reading a role
    as a root.

    A name whose value is still its own template is `UNBOUND` and not
    `MISSING`. Those are the two readings of the same string and they are
    different facts: `{DATA_ROOT}/state` is not a path that is absent, it is a
    path nobody answered for, and `Paths.unconfigured` is the list that says
    which. Treating one as the other is how a composed template becomes a
    directory somebody creates.
    """
    bound = dict(getattr(paths, "roots", None) or {})
    unconfigured = set(getattr(paths, "unconfigured", ()) or ())
    if not bound:
        return {"state": UNKNOWN, "entries": [], "coverage": {},
                "why": ("the root contract resolved no names, so which roots "
                        "exist on this node was not established. This is not "
                        "an empty disk; it is a node whose roots nobody "
                        "answered for.")}
    rows = []
    roles = set(role_names)
    for name in sorted(bound):
        if name in roles:
            # A semantic role IS one of the bound roots — `paths.role(name)`
            # reads the same mapping — so listing it under both kinds puts one
            # name on the map twice and makes the denominator count a name that
            # was never in question. The role row below carries the path AND
            # what the name means, so it is the richer of the two and the one
            # that survives.
            continue
        value = str(bound[name])
        if name in unconfigured:
            rows.append({"name": name, "kind": "root", "path": None,
                         "state": UNBOUND, "value_as_composed": value,
                         "why": ("no authority answered for this name; the "
                                 "value is its own template and is not a path "
                                 "anybody should use")})
            continue
        rows.append(_place(name, "root", Path(value)))
    for name in sorted(role_names):
        try:
            entry = paths.role(name)
        except Exception as exc:  # noqa: BLE001 - an undeclared role is a row
            rows.append({"name": name, "kind": "role", "path": None,
                         "state": UNREADABLE,
                         "why": f"{exc.__class__.__name__}: {exc}"})
            continue
        rows.append({**_place(name, "role", Path(entry["path"])),
                     "also_bound_as_a_root": name in bound,
                     "declared": entry.get("note", "")})
    for ref in sorted(refs):
        try:
            entry = paths.resolve(ref)
        except Exception as exc:  # noqa: BLE001 - an unresolvable ref is a row
            rows.append({"name": ref, "kind": "ref", "path": None,
                         "state": UNREADABLE,
                         "why": f"{exc.__class__.__name__}: {exc}"})
            continue
        rows.append({**_place(ref, "ref", Path(entry["path"])),
                     "root": entry.get("root")})
    return {"state": "READ", "entries": rows,
            "coverage": {"names": len(rows),
                         "present": sum(1 for r in rows if r["state"] == PRESENT),
                         "missing": sum(1 for r in rows if r["state"] == MISSING),
                         "unbound": sum(1 for r in rows if r["state"] == UNBOUND),
                         "unreadable": sum(1 for r in rows if r["state"] == UNREADABLE)}}


def _place(name: str, kind: str, path: Path) -> dict:
    exists = path.exists()
    return {"name": name, "kind": kind, "path": str(path),
            "state": PRESENT if exists else MISSING,
            "why": "" if exists else "the path does not exist on this node"}


def feishu_coverage(*, sources: list[dict] | None, contract: dict) -> dict:
    """The three cloud surfaces, kept apart and each counted as UNKNOWN.

    This is the NOT-LIVE adapter §7 asks for, written so that its refusal is
    readable rather than implicit. There is no client here, no token, no paging
    loop and no cursor: the counts are `UNKNOWN` with the reason, because a
    zero would say the spaces are empty and a program that never looked is not
    entitled to say that.
    """
    entry = next((c for c in contract.get("coverage") or []
                  if c.get("id") == "feishu-spaces"), None)
    surfaces = (entry or {}).get("surfaces") or []
    if not surfaces:
        raise Refused("the contract names no feishu surfaces, so this map would "
                      "report on nothing while looking complete")
    authored = {s.get("surface"): s for s in (sources or []) if isinstance(s, dict)}
    rows = []
    for surface in surfaces:
        declared = authored.get(surface)
        rows.append({
            "surface": surface,
            "state": "UNKNOWN",
            "objects": None,
            "because": (entry or {}).get("note", "").strip() or
                       "no cloud call is made by this round",
            "followed": (declared or {}).get("followed"),
            "scope": (declared or {}).get("scope"),
        })
    return {"state": "NOT_LIVE",
            "denominator": (entry or {}).get("denominator"),
            "surfaces": rows,
            "authored_sources_present": sorted(authored)}


#: Where the node's structured understanding actually lives. Under `records/`,
#: beside `projects/` rather than inside one of them, because it is a property
#: of what is understood and not of whoever is currently carrying the work.
UNDERSTANDING_RELATIVE = ("information", "understanding")

#: The one heading the node's own README states as a requirement rather than a
#: preference: `understanding/README.md` — "每份认识必须能溯源到 raw source".
#: Counted separately from the proposed skeletons because it is a rule the
#: documents' own author wrote down, and this module did not.
TRACEABILITY_HEADING = "Sources"


def understanding_coverage(*, records_root: Path, skeletons: dict | None) -> dict:
    """The 理解变化 layer: four scopes, counted, with the traceability rule.

    WHY THIS IS NOT A PROJECT DOCUMENT, which a first version of
    `document-skeletons.yaml` claimed it was. That file named
    `records/projects/<project>/context/understanding.md`. This node holds no
    such file. It holds `records/information/understanding/` with FOUR scopes —
    `people/`, `projects/`, `topics/`, `responsibilities/` — and
    `understanding/README.md` states the boundary in as many words:
    understanding is what we currently know, `raw` is what happened, and
    `projects/<p>/context` is where understanding goes only once it has stopped
    moving. So the composed path was wrong the same way the four path defects
    this module already carries were wrong: a layout inferred from a design
    document, reported about a node that has a different one.

    WHAT IT MEASURES. Per scope: whether the directory is there, how many
    documents it holds, and how many of them carry a `## Sources` section. That
    last number is the traceability red line, and it is reported as a count of
    documents rather than as a verdict on each one — the node's own people and
    responsibilities cards do not use `## Sources` at all, and reporting 5 of
    12 as defective would be this module inventing a rule one scope over.

    A directory in the understanding root that the skeleton does not name is
    reported in `undeclared_scopes` rather than dropped: a scope somebody adds
    is a scope this map should stop being complete about.
    """
    root = records_root.joinpath(*UNDERSTANDING_RELATIVE)
    if skeletons is None:
        # The skeletons file is OPTIONAL and its absence is not an error: it is
        # a declaration this round proposes, and a caller that did not point at
        # one has not made a mistake. Raising here would turn "nothing declared
        # a scope list" into a traceback, which is the loudest possible way to
        # say the quietest possible thing.
        return {"root": str(root), "state": "NOT_CHECKED",
                "scopes": [], "undeclared_scopes": [],
                "denominator": None,
                "why": ("no skeleton file is present, so the scope list is not "
                        "declared and this layer is not counted. That is an "
                        "absence of a declaration and not an empty layer")}
    declared = skeleton_of(skeletons, "understanding") or {}
    scopes = declared.get("scopes") or []
    if not scopes:
        raise Refused(
            "a skeleton file is present and declares no scopes for the "
            "understanding layer, so this map would report on nothing while "
            "looking complete. The scope list is authored in "
            "`intake/document-skeletons.yaml`; it is not derived from whatever "
            "directories happen to exist, because a scope nobody declared and "
            "a scope that was deleted would then read the same.")
    rows = []
    for scope in scopes:
        name = scope.get("id")
        directory = root / name
        documents = (sorted(directory.rglob("*.md"))
                     if directory.is_dir() else [])
        traceable = 0
        for document in documents:
            try:
                text = document.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if any(line.lstrip().startswith("#")
                   and line.lstrip().lstrip("#").strip() == TRACEABILITY_HEADING
                   for line in text.splitlines()):
                traceable += 1
        rows.append({
            "scope": name,
            "is": scope.get("is"),
            "path": str(directory),
            "state": "PRESENT" if documents or directory.is_dir()
                     else "NOT_FOUND",
            "documents": len(documents),
            f"with_{TRACEABILITY_HEADING.lower()}": traceable,
        })
    known = {r["scope"] for r in rows}
    present = set()
    if root.is_dir():
        present = {child.name for child in root.iterdir() if child.is_dir()}
    return {
        "root": str(root),
        "state": "PRESENT" if root.is_dir() else "NOT_FOUND",
        "why": ("the understanding layer is where 认识变化 is recorded and is "
                "not a per-Item document"),
        "scopes": rows,
        "denominator": f"the {len(scopes)} scopes the skeleton names",
        "undeclared_scopes": sorted(present - known),
        "traceability_heading": TRACEABILITY_HEADING,
        "traceability_because": (
            "`understanding/README.md` — 每份认识必须能溯源到 raw source. It is "
            "the node's own rule and not one this module wrote, so it is "
            "counted and not graded: the people and responsibilities cards do "
            "not use this heading, and a defect count over them would be this "
            "module inventing a standard"),
    }


def source_coverage(*, contract: dict, sources: list[dict] | None) -> dict:
    """The followed-source list: what a person follows, and how deep.

    `credential_ref` is checked to be a REFERENCE. A value in that field would
    be a credential in a repository whose policy forbids exactly that, so the
    field is validated rather than trusted — a shape that merely looks like a
    path is refused.
    """
    entry = next((c for c in contract.get("coverage") or []
                  if c.get("id") == "followed-sources"), None)
    fields = (entry or {}).get("fields") or []
    rows = []
    for index, source in enumerate(sources or []):
        if not isinstance(source, dict):
            rows.append({"index": index, "state": UNREADABLE,
                         "why": "the entry is not a mapping"})
            continue
        missing = [f for f in fields if source.get(f) in (None, "")]
        ref = source.get("credential_ref")
        bad_ref = bool(ref) and not _looks_like_a_reference(str(ref))
        rows.append({
            "identity": source.get("identity"),
            "state": MISSING if missing else ("REFUSED" if bad_ref else PRESENT),
            "missing_fields": missing,
            "why": ("`credential_ref` is not a reference: a reference is a name "
                    "or an id, and anything that could BE the secret is refused "
                    "here rather than stored" if bad_ref else ""),
            "depth": source.get("depth"),
            "publishable": source.get("publishable"),
        })
    return {"state": "READ" if sources else UNPRODUCED,
            "why": "" if sources else "no source list was authored for this project",
            "fields": list(fields), "rows": rows,
            "coverage": {"authored": len(rows),
                         "complete": sum(1 for r in rows if r["state"] == PRESENT),
                         "refused": sum(1 for r in rows if r["state"] == "REFUSED")}}


#: How long an unbroken run of characters may be before it stops reading as a
#: name. A reference is made of WORDS — `vault:feishu/tokyo-reader` splits into
#: pieces of twelve or fewer — while a credential is one continuous blob. 24 is
#: well above any word a person types into this field and well below the
#: shortest key anybody issues.
MAX_UNBROKEN_RUN = 24

_REFERENCE_SEPARATORS = "-_:/.@+~"


def _looks_like_a_reference(text: str) -> bool:
    """A reference names a secret; it is not one.

    Narrow on purpose, and narrower than the first version, which accepted
    anything under 80 characters that had no colon and no slash — so
    `sk-` followed by forty letters passed as a "reference". That is the exact
    string a check like this exists to catch, and the check was found by writing
    the test for it.

    Four shapes are refused: a long token, a credential's own vocabulary
    (`bearer `, `token=`, `-----begin`), a path INTO a vault's contents, and —
    the rule the first version was missing — any unbroken run of
    `MAX_UNBROKEN_RUN` or more characters with no separator in it. The last is
    the one that catches a key nobody has seen before, and it refuses in the
    safe direction: a person who meant a long opaque id writes it with a
    separator, and the cost of the false positive is a rewritten line, against
    the cost of the false negative being a secret in this repository.
    """
    if len(text) > 80:
        return False
    lowered = text.lower()
    for marker in ("bearer ", "token=", "secret=", "password=", "-----begin"):
        if marker in lowered:
            return False
    if text.endswith((".json", ".yaml", ".yml", ".toml", ".ini", ".env")):
        return False
    run = longest = 0
    for character in text:
        run = 0 if character in _REFERENCE_SEPARATORS else run + 1
        longest = max(longest, run)
    if longest >= MAX_UNBROKEN_RUN:
        return False
    return ":" in text or "/" not in text


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:  # pragma: no cover - the CLI owns this
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--contract", required=True)
    parser.add_argument("--boards-root", required=True)
    parser.add_argument("--records-root", required=True)
    parser.add_argument("--workspace-root", required=True,
                        help="what a relative `card_locator` is relative TO")
    parser.add_argument("--skeletons", default="")
    args = parser.parse_args(argv)
    contract = load_contract(args.contract)
    projects = load_projects(boards_root=Path(args.boards_root))
    board = compose_board(contract=contract, projects=projects,
                          records_root=Path(args.records_root),
                          workspace_root=Path(args.workspace_root),
                          skeletons=(load_skeletons(args.skeletons)
                                     if args.skeletons else None))
    print(json.dumps(board, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
