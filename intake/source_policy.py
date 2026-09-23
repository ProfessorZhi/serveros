"""Which sources are in force, by what policy, decided by which rule.

WHY THIS MODULE EXISTS
----------------------
`docs/INFORMATION-DOCUMENT-LIFECYCLE.md` §6 names `records/information/sources/`
as the place a source is DECLARED — who is followed, over what scope, why, who
owns the body, and how deep the capture goes — and names the two lists that say
something like it today: `raw/DRIFT_WATCHLIST.txt` and `not-archived.yaml`. It
then states the constraint this module's shape is built around:

    不让两份可编辑列表同时生效。

Two editable lists in force is not a duplication problem, it is an ORDERING
problem. When they disagree there is no answer to which one a collector obeys,
and the answers a reader reaches for instead — the newer file, the one that
loaded last, whichever the loop happened to open — are not rules. So every row
here carries `decided_by`: the ONE rule that decided it, at the one precedence
level that was reached. A row no rule reaches is `UNDECLARED` and not a
default, because a default that cannot be told from a decision is how a policy
nobody made gets obeyed.

The rule order is the document's, not this module's:

    real authorization/deny → 明确source政策 → 已接受对接关系 → 一般默认

and the sentence that gives it teeth is 「具体标签不能绕上级deny」: a positive
label met under a deny is not in force, and it is reported in `bypassed` rather
than dropped, because the entry that wrote it is still on disk and its author
still expects it to mean something.

WHAT IT REFUSES TO DO
---------------------
It does not key anything by a display name. §1 of `docs/DEEP-ARCHIVE-POLICY.md`
asks that the priority contacts be resolved 「不以显示名硬编码」, and the
capture tree shows why: `docx-research-outreach-sop` is the directory name of
TWO different link objects, in two different chats, so a slug is a label that
happens to be unique until it is not. This module matches declarations on
`feishu:chat:<chat_id>` / `feishu:doc:<url>`, reports the collector's slug as
`observed_as` beside the identity and never as one, and refuses a declaration
whose only key is a name.

It does not decide completeness. Message coverage, material discovery, material
landing and semantic understanding are four answers with four producers, and a
policy resolver that produced any of them would be the second authority on
them. `coverage_owner` names whose answer a row's completeness is.

It does not merge the two publish-plane registries. `heavy-media-externalized`
and `publish-excluded` sit in the same directory and answer a different
question — what may enter Git — and `docs/DEEP-ARCHIVE-POLICY.md` §8 says the
two 判据 are not merged. They are read, their state is reported, and they are
not policy inputs.

It does not guess a tenant. A declaration that pins `tenant_key` and an
observed manifest that carries a different one is a near miss reported with its
reason, not a match.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

SCHEMA = "serveros/source-policy.v1"

#: The records-repo segments this module reads. `information/sources` is the
#: desired-declaration location `INFORMATION-DOCUMENT-LIFECYCLE.md` §6 names and
#: which does not exist yet; `information/raw` is where the observed side is.
SOURCES_SEGMENTS = ("information", "sources")
RAW_SEGMENTS = ("information", "raw")

#: The five labels, verbatim from `CC-EXECUTION-PLAN.md` P1 and
#: `DEEP-ARCHIVE-POLICY.md` §2. They are one vocabulary over three axes, and
#: saying which axis a label belongs to is what lets a row carry two of them
#: without one silently standing in for the other.
POLICIES = ("NONE", "SHALLOW", "DEEP_REQUIRED", "FOLLOW", "REFERENCE")
POLICY_AXES = {
    "NONE": "exclusion",
    "SHALLOW": "depth",
    "DEEP_REQUIRED": "depth",
    "FOLLOW": "tracking",
    "REFERENCE": "product",
}

#: Which label decides what a collector does, worst first. `NONE` is a deny and
#: is first for that reason; `DEEP_REQUIRED` is an obligation and beats the
#: default. `FOLLOW` and `REFERENCE` are kept outside this order on purpose:
#: neither says how much of the body to keep, so neither can decide a depth.
DEPTH_DECIDING = ("NONE", "DEEP_REQUIRED", "SHALLOW")

#: The document's rule order, highest first. `AUTHORIZATION` is a deny that was
#: registered; `DECLARATION` is `information/sources/`; `CONTACT_RELATION` is a
#: priority contact's declared relation; `LEGACY` is the two lists this design
#: migrates away from; `GENERAL_DEFAULT` is the 一般默认 of §1.
PRECEDENCE = ("AUTHORIZATION", "DECLARATION", "CONTACT_RELATION", "LEGACY",
              "GENERAL_DEFAULT")

#: What `decided_by` may name. One per rule, so a row's sentence is checkable
#: against the record it came from.
DECIDED_BY = ("AUTHORIZATION_REGISTRY", "DECLARATION", "CONTACT_RELATION",
              "LEGACY_WATCHLIST", "GENERAL_DEFAULT")

#: The relationship a rule stands in, and the level it is decided at.
LEVEL_OF = {
    "AUTHORIZATION_REGISTRY": "AUTHORIZATION",
    "DECLARATION": "DECLARATION",
    "CONTACT_RELATION": "CONTACT_RELATION",
    "LEGACY_WATCHLIST": "LEGACY",
    "GENERAL_DEFAULT": "GENERAL_DEFAULT",
}

#: What a row is. `UNRESOLVED` is a declaration or legacy line naming an object
#: no capture object holds — the row exists so that a rule about a source that
#: was never captured appears SOMEWHERE, since a source missing from a coverage
#: report reads as a source with nothing wrong.
ROW_STATES = ("IN_FORCE", "CONFLICT", "UNDECLARED", "UNRESOLVED", "UNREADABLE")

#: A `FOLLOW` label with no follow block. Key → what the key has to say, in the
#: words of §6 of the work instruction: root object, depth, include/exclude, the
#: new-descendant policy, the history start point, the check policy, the budget
#: and the audience/data policy. All eight are required TOGETHER, because each
#: one alone is the shape of a follow and not a follow: a root with no budget is
#: a promise to crawl a subtree forever.
FOLLOW_PRODUCED = {
    "root_object": "root_object — the stable locator the follow is rooted at "
                   "(never a title: a title is not an identity)",
    "depth": "depth — how far below the root the follow reaches",
    "include": "include — what inside that range is in scope",
    "exclude": "exclude — what is out of it (may be empty; must be present)",
    "new_descendants": "new_descendants — the policy for children created after "
                       "the follow began",
    "history_start": "history_start — where the covered history begins",
    "check_policy": "check_policy — when and how the object is re-checked",
    "budget": "budget — the bound the re-checking runs under",
    "audience": "audience — who may read the local snapshot, and the data policy",
}

#: A `REFERENCE` label with no reference block. `save: FULL_COPY` is refused
#: rather than accepted: §5 of the work instruction and `INFORMATION-DOCUMENT-
#: LIFECYCLE.md` §5 both make a whole-article copy a separate permission
#: decision, so a source declaration is not the document that grants it.
REFERENCE_PRODUCED = {
    "canonical_url": "canonical_url — the body authority: the official address "
                     "of the original, not a copy of it",
    "permitted_scope": "permitted_scope — what may be read and saved from it",
    "save": "save — LINK_ONLY or SNAPSHOT",
}
REFERENCE_SAVE = ("LINK_ONLY", "SNAPSHOT")

#: Fields a declaration may carry. `identity` is the key; everything else is a
#: statement about the object and is copied into the row as declared.
DECLARED_PRODUCED = {
    "id": "id — the author's name for this declaration entry. It names the ENTRY "
          "and never the object: two entries naming one identity is a conflict "
          "reported as one",
    "identity": "identity — the stable key: provider plus chat_id or url, and "
                "optionally the tenant",
    "policy": "policy — the single label, when an entry carries one",
    "policies": "policies — the labels in force, when an entry carries more "
                "than one (a collection depth and a tracking policy are "
                "different questions about one object)",
    "scope": "scope — the declared range: history_start, history_end, budget",
    "body_authority": "body_authority — which object owns the text",
    "audience": "audience — who may read it",
    "why": "why — why this source is followed. Required: a declaration without "
           "one is a name on a list",
    "decided_by_user": "decided_by_user — when a person decided this",
    "follow": "follow — the tracking declaration, required with FOLLOW",
    "reference": "reference — the reference declaration, required with REFERENCE",
    "recorded_as": "recorded_as — the display name(s) this was resolved from. "
                   "PROVENANCE, and never a key",
    "credential_ref": "credential_ref — the REFERENCE to the credential this "
                      "source needs, and never the credential itself. "
                      "`workchain.source_coverage` refuses a value in this "
                      "field, which is the rule that makes it safe to carry",
    "publishable": "publishable — whether material from this source may enter "
                   "the release surface. §8 of DEEP-ARCHIVE-POLICY keeps the "
                   "publish plane's question apart from the collection plane's, "
                   "so it is copied beside them rather than derived from them",
}

#: Fields a contact seed may carry. `identity.open_id` is the key; `recorded_as`
#: is the name it was resolved from and stays provenance.
CONTACT_PRODUCED = {
    "id": "id — the author's name for this seed",
    "identity": "identity — provider plus open_id, and optionally the tenant",
    "recorded_as": "recorded_as — the display name this seed was resolved from",
    "relation": "relation — the accepted relation; `priority_contact` is the "
                "one that carries a policy",
    "p2p_policy": "p2p_policy — the label this contact's direct chat takes",
    "scope": "scope — the declared range for a label this seed puts in force; "
             "required with DEEP_REQUIRED for the same reason a declaration "
             "needs it, because a deep obligation is deep whoever imposed it",
    "project_groups": "project_groups — the small project groups that take the "
                      "same label, each named by its OWN stable identity "
                      "(`{provider: …, chat_id: …}`). Named by identity and not "
                      "by a declaration entry id: a group the relation covers "
                      "is a group, and whether anyone has also declared it is a "
                      "separate question that must not be able to decide which "
                      "group the relation was about",
    "decided_by_user": "decided_by_user — when the relation was accepted",
    "why": "why — the basis the relation was accepted on",
}

#: Every key of a rule. A rule without its evidence is an assertion.
#:
#: `labels` is a LIST, and a rule that puts nothing in force carries an empty
#: one. That is how the exclusion registry's `reclassified` block is reported:
#: it is a fact about the object that a reader has to see, and it forbids
#: nothing — an entry that clears a deny is not itself a policy, and a rule with
#: `labels: []` is a rule that decides nothing rather than a rule that decides
#: `NONE`.
RULE_PRODUCED = {
    "decided_by": "decided_by — which rule this is",
    "level": "level — the precedence level it is decided at",
    "labels": "labels — the policies it puts in force; empty when it puts none",
    "why": "why — the sentence a reader can check against the record",
    "evidence": "evidence — the record this rule was read from",
}

#: Every key of a resolved row. `no_unsourced` refuses an emitted row carrying
#: anything not here, so a field added at a call site is a recorded decision
#: rather than a new column.
ROW_PRODUCED = {
    "object": "object — the capture object's own path under information/raw, "
              "which is how a reader looks the file up and is not a policy input",
    "role": "role — capture_coverage's role_of: chat or link, from the layout",
    "shape": "shape — capture_coverage's shape_of: from the manifest's keys",
    "identity": "identity — the stable identity, or null when the manifest "
                "carries none",
    "observed_as": "observed_as — the collector's own directory name, reported "
                   "beside the identity and never used as one",
    "label": "label — the chat name or document title, for a human",
    "state": "state — one of ROW_STATES",
    "policies": "policies — the labels in force, sorted",
    "policy": "policy — the label that decides what collection does, or null",
    "axis": "axis — the axis `policy` belongs to, or null",
    "axes": "axes — every axis in force",
    "decided_by": "decided_by — the rule that decided this row, or null",
    "why": "why — the sentences, one per fact a reader has to see",
    "rules": "rules — every rule that reached this object, decided one first",
    "bypassed": "bypassed — labels met under a deny: still on disk, not in force",
    "declared": "declared — the declaration entry's own fields, copied",
    "follow": "follow — the follow block, or its absence and why",
    "reference": "reference — the reference block, or its absence and why",
    "migration": "migration — what this row still needs before it is a "
                 "declaration the design names",
    "same_identity_as": "same_identity_as — the other capture objects holding "
                        "this same identity. `docs/DEEP-ARCHIVE-POLICY.md` §3 "
                        "wants both discovery edges kept when one document is "
                        "referenced twice, so the rows stay two and the fact "
                        "that they are one source is stated here rather than "
                        "collapsed by a deduplication",
    "coverage_owner": "coverage_owner — whose answer this object's completeness is",
}

SUMMARY_PRODUCED = {
    "objects": "objects — the capture objects read (the denominator, from "
               "capture_coverage and not counted here)",
    "rows": "rows — every row emitted, including rows for objects no capture "
            "holds",
    "by_state": "by_state — rows per ROW_STATE",
    "by_policy": "by_policy — rows per label in force",
    "by_decided_by": "by_decided_by — rows per rule",
    "unusable_declarations": "unusable_declarations — entries the declaration "
                             "reader could not key, with the reason. Reported as "
                             "a count because each one is an object that may be "
                             "missing from `in force`",
    "near_misses": "near_misses — declarations that matched an object on its key "
                   "and were then refused on a second fact (a tenant, a "
                   "provider). A near miss is what a policy written for one "
                   "tenant's chat looks like just before it lands on another's",
}


class Refused(RuntimeError):
    """This reader will not answer, and guessing is not an option.

    Raised for a defect in the READER: an emitted key no table names, a missing
    root. Deliberately not the exception for a record this reader cannot parse —
    a list that failed to parse is a fact about the list, and reporting it as a
    reader defect would blame the file for the bug.
    """


class Malformed(Refused):
    """One declaration is not a declaration. A fact about the file, not the reader."""


def sources_root(records_root: Path | str) -> Path:
    """`<records_root>/information/sources` — composed here so callers cannot differ."""
    return Path(records_root).joinpath(*SOURCES_SEGMENTS)


def raw_root(records_root: Path | str) -> Path:
    """`<records_root>/information/raw`."""
    return Path(records_root).joinpath(*RAW_SEGMENTS)


def no_unsourced(mapping: dict, table: dict, where: str) -> dict:
    """`mapping` if every key is in `table`, else a refusal naming the orphans.

    Applied where a record is EMITTED rather than where each key is produced,
    for the reason `capture_coverage` gives: a guard only at the producer
    protects the producer, and the record is the thing that gets read.
    """
    unexpected = sorted(set(mapping) - set(table))
    if unexpected:
        raise Refused(
            f"this reader would emit {', '.join(f'`{where}.{key}`' for key in unexpected)}, "
            f"which the producer table does not name a source for; add the "
            f"producer or drop the field")
    return mapping


def _read_yaml(path: Path) -> tuple[dict | None, str]:
    """A YAML mapping, or `(None, why)`. A file that cannot be read is a fact."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return None, f"cannot read {path}: {exc}"
    if document is None:
        return None, (f"{path} is empty: an empty file declares nothing, which "
                      f"is not the same as a file that declares no sources")
    if not isinstance(document, dict):
        return None, (f"{path} holds {type(document).__name__}, not a mapping; a "
                      f"list of sources that cannot be read as fields is not a "
                      f"list of sources")
    return document, ""


def _slug_of(record_path: str) -> str:
    """The collector's own directory name for this object, as observed.

    For a link this is the slug the collector chose and it is not stable — the
    capture tree holds two `docx-research-outreach-sop` directories under two
    different chats. For a chat the directory name happens to be the chat id the
    manifest also carries; the manifest is still what the identity is read from,
    because a name that is usually the identity is exactly the kind of key that
    works until it does not.
    """
    parts = Path(record_path).parts
    return parts[-2] if len(parts) >= 2 else record_path


def identity_of(document: dict, shape: str, record_path: str) -> dict:
    """The stable identity of one capture object, or a stated absence.

    Read from the manifest and never from the path. A chat capture's key is its
    `chat_id`; a document capture's is its `url`. Both are what the providers
    treat as identity and what the legacy lists do not carry, which is the whole
    reason this module reads the manifests rather than the directory names.
    """
    slug = _slug_of(record_path)
    if shape == "chat_capture":
        chat = document.get("chat") if isinstance(document.get("chat"), dict) else {}
        contact = document.get("contact") if isinstance(document.get("contact"), dict) else {}
        chat_id = chat.get("chat_id")
        identity = {
            "kind": "chat",
            "provider": "feishu",
            "key": key_of({"provider": "feishu", "chat_id": chat_id}),
            "chat_id": chat_id,
            "chat_type": chat.get("chat_type"),
            "tenant_key": contact.get("tenant_key"),
            "open_id": contact.get("open_id"),
            "label": chat.get("name"),
            "observed_as": slug,
            "why": "" if chat_id else
                   "the manifest carries no `chat.chat_id`: this capture object "
                   "has no stable identity, so no declaration can be matched to "
                   "it and none was",
        }
        return identity
    url = document.get("url")
    identity = {
        "kind": "document",
        "provider": "feishu",
        "key": key_of({"provider": "feishu", "url": url}),
        "url": url,
        "type": document.get("type"),
        "tenant_key": None,
        "open_id": None,
        "label": document.get("title"),
        "observed_as": slug,
        "why": "" if url else
               "the manifest carries no `url`: a link object with no locator has "
               "no identity beyond the directory it was written to, and a "
               "directory name is not one",
    }
    return identity


def _unusable(entry, where: str) -> dict:
    """One entry this reader will not key, with the reason a person can act on."""
    if not isinstance(entry, dict):
        return {"entry": None, "id": None, "why":
                f"{where}: the entry is {type(entry).__name__}, not a mapping"}
    identity = entry.get("identity")
    if not isinstance(identity, dict):
        named = [k for k in ("name", "display_name", "title", "chat_name")
                 if entry.get(k)]
        return {"entry": entry.get("id"), "id": entry.get("id"), "why":
                (f"{where}: {entry.get('id') or 'an entry'} carries "
                 f"{'`' + named[0] + '`' if named else 'no `identity`'} and no "
                 f"stable identity. A declaration keyed by a display name is not "
                 f"a declaration: `docx-research-outreach-sop` names two "
                 f"different objects in this very tree. Give it "
                 f"`identity: {{provider: …, chat_id: …}}` or "
                 f"`{{provider: …, url: …}}`")}
    provider = identity.get("provider")
    keys = [name for name in ("chat_id", "url", "open_id") if identity.get(name)]
    if not provider:
        return {"entry": entry.get("id"), "id": entry.get("id"), "why":
                f"{where}: `identity` names no `provider`, so the key it "
                f"composes is not one a provider's own id space can be checked "
                f"against"}
    if len(keys) != 1:
        return {"entry": entry.get("id"), "id": entry.get("id"), "why":
                f"{where}: `identity` carries "
                f"{' and '.join(f'`{k}`' for k in keys) if keys else 'no key at all'}; "
                f"an object is a chat, a document or a person and one "
                f"declaration is about one of them, keyed by one of them"}
    return {}


def _readable_entry(entry, where: str) -> tuple[dict, list[str]]:
    """The entry's labels, or the unusable item that replaces it.

    Every reason a declaration is not one this reader can put in force is
    decided HERE, when the file is read, and not when the entry is matched to a
    capture object. There are two defects a match-time check has and a read-time
    one does not, and both were found by writing the tests:

      * an entry whose labels cannot be read and whose object no capture holds
        would be reported as "declared but not captured" and its unreadable
        labels would go unreported — the count of unusable rules would then be
        a count over the objects that happened to be captured;
      * one broken entry matched by two capture objects (one document hold,
        two discovery edges) would be counted TWICE, in a field whose whole
        purpose is to be the number of things a person has to fix.
    """
    broken = _unusable(entry, where)
    if broken:
        return broken, []
    labels, why = _entry_labels(entry)
    if not labels:
        return {"entry": entry.get("id"), "id": entry.get("id"),
                "why": f"{where}: {why}"}, []
    blocks = _entry_blocks_ok(entry, labels)
    if blocks:
        return {"entry": entry.get("id"), "id": entry.get("id"),
                "why": f"{where}: {blocks}"}, []
    return {}, labels


def _readable_contact(contact, where: str) -> dict:
    """`{}` if this contact seed's label is one it can put in force, else the reason."""
    broken = _unusable(contact, where)
    if broken:
        return broken
    if contact.get("p2p_policy") is None:
        return {}
    labels = [contact["p2p_policy"]]
    reason = _label_error(labels) or _entry_blocks_ok(contact, labels)
    if reason:
        return {"entry": contact.get("id"), "id": contact.get("id"),
                "why": f"{where}: {reason}"}
    return {}


def declarations(records_root: Path | str) -> dict:
    """Every declaration under `information/sources/`, or the state of none.

    An absent directory is its own state and not an empty list. Today that is
    the state: the design names this location and nothing has been authored
    there, so `ABSENT` is the honest reading and `entries: []` would be a
    declaration that every source is undeclared.
    """
    root = sources_root(records_root)
    if not root.is_dir():
        return no_unsourced({
            "state": "ABSENT", "path": str(root), "files": [],
            "entries": [], "contacts": [], "unusable": [], "why":
            (f"{root} does not exist. The design names it as the place a source "
             f"is declared and no declaration has been authored there yet, so "
             f"every row below is decided by a legacy list or the general "
             f"default — which is a statement about this tree, not a statement "
             f"that nothing is declared"),
        }, DECLARATIONS_PRODUCED, "declarations")
    files = sorted(p for p in root.rglob("*")
                   if p.suffix in (".yaml", ".yml") and p.is_file())
    entries: list[dict] = []
    contacts: list[dict] = []
    unusable: list[dict] = []
    unreadable: list[dict] = []
    for path in files:
        document, why = _read_yaml(path)
        where = path.relative_to(root).as_posix()
        if document is None:
            unreadable.append({"file": where, "why": why})
            continue
        raw_entries = document.get("entries")
        if raw_entries is not None and not isinstance(raw_entries, list):
            unreadable.append({"file": where, "why":
                               "`entries` is not a list"})
            continue
        for entry in (raw_entries or []):
            broken, labels = _readable_entry(entry, f"{where}#entries")
            if broken:
                unusable.append(broken)
            else:
                entries.append({**entry, "_file": where, "_labels": labels})
        raw_contacts = document.get("contacts")
        if raw_contacts is not None and not isinstance(raw_contacts, list):
            unreadable.append({"file": where, "why": "`contacts` is not a list"})
            continue
        for contact in (raw_contacts or []):
            broken = _readable_contact(contact, f"{where}#contacts")
            if broken:
                unusable.append(broken)
            else:
                contacts.append({**contact, "_file": where})
    state = "READ" if files else "EMPTY"
    return no_unsourced({
        "state": state if not unreadable else "PARTIAL",
        "path": str(root),
        "files": [p.relative_to(root).as_posix() for p in files],
        "entries": entries,
        "contacts": contacts,
        "unusable": unusable,
        "unreadable": unreadable,
        "why": "" if not unreadable else
               f"{len(unreadable)} declaration file(s) could not be read; the "
               f"rows below are decided without them",
    }, DECLARATIONS_PRODUCED, "declarations")


def watchlist(records_root: Path | str) -> dict:
    """`raw/DRIFT_WATCHLIST.txt` — the legacy follow list, as a migration source.

    One `link_id` per line, matching a `links/<link-id>/` directory name. A line
    is therefore a SLUG and not an identity, and this reader reports the slug it
    matched on rather than promoting it into one: the resolution to a real
    object happens in `resolve`, where two objects can share the slug and the
    line is then reported as ambiguous instead of being applied to both.
    """
    path = raw_root(records_root) / "DRIFT_WATCHLIST.txt"
    if not path.is_file():
        return no_unsourced({
            "state": "ABSENT", "path": str(path), "keys": [], "why":
            f"{path} does not exist",
        }, WATCHLIST_PRODUCED, "watchlist")
    keys = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return no_unsourced({
            "state": "UNREADABLE", "path": str(path), "keys": [], "why":
            f"cannot read {path}: {exc}",
        }, WATCHLIST_PRODUCED, "watchlist")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        keys.append(stripped)
    return no_unsourced({
        "state": "READ", "path": str(path), "keys": keys, "why":
        ("" if keys else
         "the file holds no un-commented line: it is a watchlist of nothing, "
         "which is not the same as a watchlist that was not read"),
    }, WATCHLIST_PRODUCED, "watchlist")


def not_archived(records_root: Path | str) -> dict:
    """`not-archived.yaml` — the registered exclusions, as denies.

    Read as `AUTHORIZATION`: the registry answers 「账号里确实存在、但我们决定
    不把内容收进 raw/」, which is a denial of collection and not a label about
    depth. Its `reclassified` block is the part that must not be lost — an entry
    moved from `machine_named` to `source` is a recorded judgement that the
    object IS a source, so a resolver that read `reclassified` as an exclusion
    would deny exactly the two objects a person overruled it on.
    """
    path = raw_root(records_root).parent / "not-archived.yaml"
    if not path.is_file():
        return no_unsourced({
            "state": "ABSENT", "path": str(path), "denied": [],
            "reclassified": [], "denominator": None, "why":
            f"{path} does not exist: no exclusion is registered and none is "
            f"assumed",
        }, NOT_ARCHIVED_PRODUCED, "not_archived")
    document, why = _read_yaml(path)
    if document is None:
        return no_unsourced({
            "state": "UNREADABLE", "path": str(path), "denied": [],
            "reclassified": [], "denominator": None, "why": why,
        }, NOT_ARCHIVED_PRODUCED, "not_archived")
    denied = []
    for entry in (document.get("entries") or []):
        if not isinstance(entry, dict):
            continue
        chat_gid = entry.get("chat_gid")
        if not chat_gid:
            continue
        denied.append({
            "key": key_of({"provider": "feishu", "chat_id": chat_gid}),
            "reason": entry.get("reason"),
            "name": entry.get("name"),
            "observed": entry.get("observed"),
            "scope": entry.get("scope"),
        })
    reclassified = []
    for entry in (document.get("reclassified") or []):
        if not isinstance(entry, dict):
            continue
        reclassified.append({
            "key": key_of({"provider": "feishu",
                           "chat_id": entry.get("chat_gid")}),
            "name": entry.get("name"),
            "was": entry.get("was"),
            "now": entry.get("now"),
            "why": entry.get("why"),
        })
    total = document.get("total")
    by_reason = document.get("by_reason")
    denominator = None
    if isinstance(total, int):
        denominator = {
            "excluded": total,
            "by_reason": by_reason if isinstance(by_reason, dict) else None,
            "note": ("the registry's own count. It is reported so that a row "
                     "decided by the general default can be read against the "
                     "denominator the exclusions were closed over, instead of "
                     "against nothing"),
        }
    return no_unsourced({
        "state": "READ", "path": str(path), "denied": denied,
        "reclassified": reclassified, "denominator": denominator, "why": "",
    }, NOT_ARCHIVED_PRODUCED, "not_archived")


def observed(records_root: Path | str, objects: list[dict]) -> list[dict]:
    """One identity per capture object, from `capture_coverage`'s own list.

    `objects` is that module's output and is REQUIRED: it owns the denominator,
    and a second walk here would be a second answer to how many capture objects
    this tree holds. The manifest is opened again for its identity fields, which
    is a different question from coverage and the only way to ask it — the
    coverage record deliberately does not carry the contact block.
    """
    root = raw_root(records_root)
    out = []
    for record in objects:
        record_path = record.get("path")
        shape = record.get("shape")
        if not record_path:
            continue
        identity = {"kind": None, "provider": None, "key": None, "label": None,
                    "observed_as": _slug_of(record_path), "tenant_key": None,
                    "open_id": None, "chat_id": None, "url": None,
                    "chat_type": None, "why": "this record carries no `path`, so "
                    "there is no manifest to read an identity from"}
        if shape in ("chat_capture", "document_capture"):
            try:
                document = json.loads((root / record_path).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                identity["why"] = f"cannot read {record_path}: {exc}"
            else:
                if isinstance(document, dict):
                    identity = identity_of(document, shape, record_path)
                else:
                    identity["why"] = (f"{record_path} holds "
                                       f"{type(document).__name__}, not a manifest")
        out.append({"object": record_path, "role": record.get("role"),
                    "shape": shape, "identity": identity})
    return out


def _label_error(labels) -> str:
    """Why this label set is not one this reader can put in force, or `''`.

    One place, because the same five-word vocabulary is written by a source
    declaration and by a contact seed's `p2p_policy`, and a label that is not
    one of the five must be refused whoever wrote it: a label put in force is
    an instruction to a collector, and `DEEP` is not an instruction.
    """
    wrong_type = [label for label in labels if not isinstance(label, str)]
    if wrong_type:
        return f"a label is {type(wrong_type[0]).__name__}, not a label"
    unknown = sorted(set(labels) - set(POLICIES))
    if unknown:
        return (f"{', '.join(f'`{u}`' for u in unknown)} "
                f"{'is not one' if len(unknown) == 1 else 'are not'} of "
                f"{', '.join(POLICIES)}")
    return ""


def _entry_labels(entry) -> tuple[list[str], str]:
    """The labels an entry declares, or the reason it declares none usable."""
    single = entry.get("policy")
    many = entry.get("policies")
    labels: list[str] = []
    if single is not None:
        if not isinstance(single, str):
            return [], f"`policy` is {type(single).__name__}, not a label"
        labels.append(single)
    if many is not None:
        if not isinstance(many, list) or not all(isinstance(x, str) for x in many):
            return [], "`policies` is not a list of labels"
        labels.extend(many)
    if not labels:
        return [], ("the entry declares no `policy` and no `policies`, so there "
                    "is nothing for it to put in force")
    broken = _label_error(labels)
    if broken:
        return [], broken
    return sorted(set(labels)), ""


def _entry_blocks_ok(entry, labels: list[str]) -> str:
    """Whether the blocks a label requires are there. Returns a reason or `''`."""
    if "DEEP_REQUIRED" in labels:
        scope = entry.get("scope")
        if not isinstance(scope, dict):
            return ("the entry puts DEEP_REQUIRED in force and carries no "
                    "`scope`. §2 of DEEP-ARCHIVE-POLICY makes a deep archive an "
                    "obligation to complete a material closure, and an "
                    "obligation with no declared range is an obligation to "
                    "archive everything")
        missing = sorted({"history_start", "budget"} - set(scope))
        if missing:
            return (f"`scope` is missing {', '.join(f'`{m}`' for m in missing)}; "
                    f"§6 states a backfill 说明历史起点/终点…页/字节/时间预算, and a "
                    f"deep archive whose range and budget are undeclared cannot "
                    f"be told from an infinite one")
    if "FOLLOW" in labels:
        block = entry.get("follow")
        if not isinstance(block, dict):
            return ("the entry puts FOLLOW in force and carries no `follow` "
                    "block; a follow is eight declarations and not a label, so "
                    "the entry is refused rather than read as 'follows whatever "
                    "it is'")
        missing = sorted(set(FOLLOW_PRODUCED) - set(block))
        if missing:
            return (f"`follow` is missing {', '.join(f'`{m}`' for m in missing)}; "
                    f"a root with no budget is a promise to crawl a subtree "
                    f"forever, and each of the eight is required together")
    if "REFERENCE" in labels:
        block = entry.get("reference")
        if not isinstance(block, dict):
            return ("the entry puts REFERENCE in force and carries no "
                    "`reference` block: a reference names the body authority "
                    "and what may be saved, and a label without them is a "
                    "bookmark")
        if block.get("adopt") is True or block.get("adopted") is True:
            return ("`reference.adopt` is true. Adoption is not a property of a "
                    "source: it is a design change decided by B5/B6 against the "
                    "accepted baseline, and a declaration that adopts would be "
                    "the reference library writing into canon")
        save = block.get("save")
        if save not in REFERENCE_SAVE:
            return (f"`reference.save` is {save!r} and not one of "
                    f"{', '.join(REFERENCE_SAVE)}; a whole-article copy needs a "
                    f"permission and a material-coverage decision of its own, "
                    f"so this is not the declaration that grants it")
        missing = sorted(set(REFERENCE_PRODUCED) - set(block))
        if missing:
            return (f"`reference` is missing {', '.join(f'`{m}`' for m in missing)}")
    return ""


def key_of(identity: dict) -> str | None:
    """The stable key an identity composes to, or `None` when it composes none.

    One place, because the same key has to be produced from a declaration, an
    observed manifest and an exclusion registry entry, and three hand-written
    f-strings are three chances for one of them to disagree by a separator.
    """
    provider = identity.get("provider") or "feishu"
    if identity.get("chat_id"):
        return f"{provider}:chat:{identity['chat_id']}"
    if identity.get("url"):
        return f"{provider}:doc:{identity['url']}"
    if identity.get("open_id"):
        return f"{provider}:person:{identity['open_id']}"
    return None


def _identity_match(rule_identity: dict, observed_identity: dict) -> tuple[bool, str]:
    """Whether a declared identity names this object, and a near miss otherwise.

    The near miss is the point. A declaration that pins `tenant_key` and an
    observed manifest that carries a different one are two different answers
    about one key, and matching them anyway is how a policy written for one
    tenant's chat lands on another's.
    """
    provider = rule_identity.get("provider")
    if provider and observed_identity.get("provider") and \
            provider != observed_identity["provider"]:
        return False, (f"declared provider `{provider}` and the manifest's is "
                       f"`{observed_identity['provider']}`")
    if rule_identity.get("chat_id"):
        if rule_identity["chat_id"] != observed_identity.get("chat_id"):
            return False, ""
    elif rule_identity.get("url"):
        if rule_identity["url"] != observed_identity.get("url"):
            return False, ""
    elif rule_identity.get("open_id"):
        if rule_identity["open_id"] != observed_identity.get("open_id"):
            return False, ""
    else:
        return False, ""
    declared_tenant = rule_identity.get("tenant_key")
    seen_tenant = observed_identity.get("tenant_key")
    if declared_tenant and seen_tenant and declared_tenant != seen_tenant:
        return False, ("the declaration pins a `tenant_key` and the manifest "
                       "carries a different one; a key that is stable within a "
                       "tenant is not stable across tenants")
    return True, ""


def _rule(decided_by: str, labels, why: str, evidence: dict) -> dict:
    return no_unsourced({
        "decided_by": decided_by, "level": LEVEL_OF[decided_by],
        "labels": sorted(set(labels or ())), "why": why, "evidence": evidence,
    }, RULE_PRODUCED, "rule")


def _rules_for(object_row: dict, decl: dict, legacy_deny: dict,
               matched: dict) -> list[dict]:
    """Every rule that reaches this object, from the records and not from names."""
    identity = object_row["identity"]
    rules: list[dict] = []
    key = identity.get("key")

    # 1. AUTHORIZATION — a registered exclusion. A deny, and the only level that
    #    forbids rather than labels. The `reclassified` block is consulted FIRST:
    #    a chat moved from `machine_named` to `source` is a person's judgement
    #    that it IS a source, and reading the entry that carries it as an
    #    exclusion would deny exactly the object the judgement was about.
    reclassified = {row["key"]: row for row in legacy_deny["reclassified"]}
    denied = {row["key"]: row for row in legacy_deny["denied"]}
    if key in reclassified and key not in denied:
        rules.append(_rule(
            "AUTHORIZATION_REGISTRY", [],
            f"the exclusion registry lists this object as `{reclassified[key]['was']}` "
            f"and records it RECLASSIFIED to `{reclassified[key]['now']}`: the "
            f"entry in this registry is the history of a judgement, and the "
            f"judgement is that this object is a source",
            {"registry": legacy_deny["path"], "key": key,
             "reclassified": reclassified[key]}))
    if key in denied:
        rules.append(_rule(
            "AUTHORIZATION_REGISTRY", ["NONE"],
            f"registered as not archived, reason `{denied[key]['reason']}`: the "
            f"content exists and was decided against collecting, and a label on "
            f"a lower level cannot bypass the deny",
            {"registry": legacy_deny["path"], "key": key, "entry": denied[key]}))

    # 2. DECLARATION — the design's own location.
    for entry in decl["entries"]:
        ok, near = _identity_match(entry["identity"], identity)
        if not ok:
            if near:
                matched.setdefault("near_misses", []).append({
                    "entry": entry.get("id"), "file": entry["_file"],
                    "object": object_row["object"], "why": near})
            continue
        labels = entry["_labels"]
        rules.append(_rule(
            "DECLARATION", labels,
            f"declared at {entry['_file']}: {entry.get('why') or '(no `why` given)'}",
            {"entry": entry.get("id"), "file": entry["_file"],
             "labels": labels, "declared": _declared_of(entry)}))

    # 3. CONTACT_RELATION — an accepted relation, resolved on the contact's
    #    stable open_id and not on a name.
    for contact in decl["contacts"]:
        identity_of_contact = contact["identity"]
        label = contact.get("p2p_policy")
        # The label's own readability and the blocks it requires were decided
        # when the file was read (`_readable_contact`), so everything reaching
        # here is a relation this reader can put in force. What remains is a
        # fact about ONE chat's row: a relation that names no label for its
        # direct chat puts nothing in force there, and saying so on the row is
        # more use than a silent default.
        if identity.get("open_id") and \
                identity_of_contact.get("open_id") == identity.get("open_id") and \
                identity.get("chat_type") == "p2p":
            if not label:
                matched.setdefault("unusable", []).append({
                    "entry": contact.get("id"), "file": contact["_file"],
                    "why": "a priority contact with no `p2p_policy` puts no "
                           "label in force"})
                continue
            rules.append(_rule(
                "CONTACT_RELATION", [label],
                f"the accepted relation to {contact.get('recorded_as') or contact.get('id')} "
                f"resolves to this chat: their direct chat is the one the "
                f"relation covers, and the key it was resolved on is the "
                f"contact's own id rather than the name it was written as",
                {"contact": contact.get("id"), "relation": contact.get("relation"),
                 "recorded_as": contact.get("recorded_as"),
                 "basis": "p2p", "decided_by_user": contact.get("decided_by_user")}))
        for group in (contact.get("project_groups") or []):
            if not isinstance(group, dict):
                matched.setdefault("unusable", []).append({
                    "entry": contact.get("id"), "file": contact["_file"],
                    "why": f"`project_groups` holds {type(group).__name__}, not "
                           f"an identity mapping. §1 of DEEP-ARCHIVE-POLICY "
                           f"resolves the contacts 按稳定身份, and a group "
                           f"written as a name is the one thing that cannot be"})
                continue
            ok, near = _identity_match(group, identity)
            if not ok:
                if near:
                    matched.setdefault("near_misses", []).append({
                        "entry": contact.get("id"), "file": contact["_file"],
                        "object": object_row["object"],
                        "why": f"a `project_groups` entry: {near}"})
                continue
            if not label:
                continue
            rules.append(_rule(
                "CONTACT_RELATION", [label],
                f"the accepted relation to {contact.get('recorded_as') or contact.get('id')} "
                f"names this project group by its own id, which is what §1 of "
                f"DEEP-ARCHIVE-POLICY allows and what 「大通用群不因包含这些联系人"
                f"全部deep」 excludes. A group that also declares a policy of "
                f"its own is decided by that declaration instead, because the "
                f"declaration level is above this one",
                {"contact": contact.get("id"), "relation": contact.get("relation"),
                 "recorded_as": contact.get("recorded_as"),
                 "basis": "project_group",
                 "group_key": key_of(group),
                 "decided_by_user": contact.get("decided_by_user")}))

    # 4. LEGACY — the follow watchlist, a slug list being migrated.
    for legacy_key, hits in matched["watchlist_hits"].items():
        if object_row["object"] not in hits:
            continue
        if len(hits) > 1:
            # A line matching two objects is applied to NEITHER, and `resolve`
            # emits an UNRESOLVED row for it instead. This is not an oversight
            # that the reporting covers: a rule put in force is an instruction
            # to a collector, and putting `FOLLOW` on both objects would obey a
            # line the person wrote about one of them.
            continue
        rules.append(_rule(
            "LEGACY_WATCHLIST", ["FOLLOW"],
            f"listed in DRIFT_WATCHLIST.txt as `{legacy_key}`, which is a "
            f"collector's slug and not an identity: it was resolved to this "
            f"object through the directory name, and this is the only capture "
            f"object that directory name fits",
            {"list": "DRIFT_WATCHLIST", "key": legacy_key,
             "matched_by": "directory_name", "objects": sorted(hits)}))
    return rules


def _declared_of(entry) -> dict:
    """The declaration's own fields, copied — and its labels resolved to a list."""
    declared = {k: v for k, v in entry.items() if not k.startswith("_")}
    declared["labels"] = list(entry["_labels"])
    return declared


def _axis_conflict(labels: list[str]) -> dict | None:
    """Two labels on one axis, which is one question answered twice."""
    by_axis: dict[str, list[str]] = {}
    for label in labels:
        by_axis.setdefault(POLICY_AXES[label], []).append(label)
    for axis, seen in sorted(by_axis.items()):
        if len(seen) > 1:
            return {"axis": axis, "labels": sorted(seen),
                    "why": f"two labels on the {axis} axis are in force for one "
                           f"object; an object has one answer to '{axis}', and "
                           f"picking the stronger one would be this reader "
                           f"deciding a question that was declared twice"}
    return None


def _decide(labels: list[str]) -> tuple[str | None, str | None]:
    """Which label decides, and on which axis. `DEPTH_DECIDING` in order."""
    for candidate in DEPTH_DECIDING:
        if candidate in labels:
            return candidate, POLICY_AXES[candidate]
    for candidate in ("FOLLOW", "REFERENCE"):
        if candidate in labels:
            return candidate, POLICY_AXES[candidate]
    return None, None


def resolve(records_root: Path | str, objects: list[dict]) -> dict:
    """One row per capture object, plus a row per declaration nothing holds.

    `objects` is `capture_coverage.read_root(records)["objects"]` — that module
    owns the denominator and this one does not count it again.
    """
    root = Path(records_root)
    decl = declarations(root)
    legacy_watch = watchlist(root)
    legacy_deny = not_archived(root)
    seen = observed(root, objects)

    # The watchlist is resolved ONCE, here, so that a slug matching two objects
    # is visible as one fact rather than as two rules that each look fine.
    watchlist_hits: dict[str, list[str]] = {}
    for object_row in seen:
        for legacy_key in legacy_watch["keys"]:
            if object_row["identity"].get("observed_as") == legacy_key:
                watchlist_hits.setdefault(legacy_key, []).append(object_row["object"])

    matched = {"watchlist_hits": watchlist_hits, "near_misses": [], "unusable": []}

    rows = []
    for object_row in seen:
        identity = object_row["identity"]
        rules = _rules_for(object_row, decl, legacy_deny, matched)
        rows.append(_row(object_row, rules, decl, legacy_deny))

    # A declaration or a legacy line naming an object no capture holds. Without
    # these rows a rule about a source that was never captured — or was captured
    # and has since gone — would appear nowhere, and a source missing from a
    # policy report reads as a source with nothing wrong.
    resolved_keys = {row["identity"].get("key") for row in seen}
    for entry in decl["entries"]:
        key = key_of(entry["identity"])
        if key in resolved_keys:
            continue
        rows.append(_unresolved_row(
            key or f"declaration:{entry.get('id')}", "DECLARATION",
            f"declared at {entry['_file']} and no capture object in this tree "
            f"holds it. A declaration is a statement about an object; whether "
            f"the object was ever captured is the capture side's answer, and "
            f"its answer here is that it was not"))
    for legacy_key, hits in sorted(watchlist_hits.items()):
        if len(hits) > 1:
            rows.append(_unresolved_row(
                f"legacy:{legacy_key}", "LEGACY_WATCHLIST",
                f"DRIFT_WATCHLIST.txt line `{legacy_key}` matches {len(hits)} "
                f"capture objects, because it is a directory name and two chats "
                f"can hold a directory of the same name. Which object the line "
                f"means is a question for the person who wrote it; applying it "
                f"to both would put a follow nobody declared on one of them"))
    for legacy_key in legacy_watch["keys"]:
        if legacy_key in watchlist_hits:
            continue
        rows.append(_unresolved_row(
            f"legacy:{legacy_key}", "LEGACY_WATCHLIST",
            f"DRIFT_WATCHLIST.txt line `{legacy_key}` matches no capture object "
            f"under information/raw: the file names a snapshot the capture tree "
            f"does not hold"))

    # One identity, several capture objects: §3 of DEEP-ARCHIVE-POLICY keeps
    # both discovery edges when one document is referenced from two chats, and
    # §4 states the general case — 同内容被转发两次可复用合法字节，但两条发现边都
    # 留. The rows therefore stay two and the fact that they are ONE source is
    # stated on both, rather than a deduplication that would keep one edge.
    by_key: dict[str, list[str]] = {}
    for row in rows:
        key = (row["identity"] or {}).get("key")
        if key:
            by_key.setdefault(key, []).append(row["object"])
    for row in rows:
        key = (row["identity"] or {}).get("key")
        if key and len(by_key[key]) > 1:
            row["same_identity_as"] = sorted(o for o in by_key[key]
                                             if o != row["object"])

    contradictions = _contradictions(rows, legacy_deny)
    summary = _summarize(rows, len(objects), decl, matched)
    document = {
        "schema": SCHEMA,
        "root": str(root),
        "declarations": decl,
        "legacy": {"watchlist": legacy_watch, "not_archived": legacy_deny},
        "planes": _planes(root),
        "contradictions": contradictions,
        "rows": rows,
        "summary": summary,
    }
    return no_unsourced(document, DOCUMENT_PRODUCED, "policy")


def _contradictions(rows: list[dict], legacy_deny: dict) -> list[dict]:
    """Places where two records about one object disagree.

    There is one today and it is the crossing of the two editable lists the
    design is migrating: an exclusion registry entry and a capture object can
    only be about the same chat by one of the two being wrong — the registry
    says the content was decided against collecting, and the tree holds it. That
    is a sentence a person has to settle, and reporting it is the difference
    between two lists in force and two lists that contradict each other.
    """
    held = {(row["identity"] or {}).get("key") for row in rows}
    out = []
    for entry in legacy_deny["denied"]:
        if entry["key"] in held:
            out.append({
                "kind": "DENIED_BUT_CAPTURED",
                "key": entry["key"],
                "why": (f"`{entry['key']}` is registered as not archived for "
                        f"reason `{entry['reason']}` and a capture object in "
                        f"this tree holds it. Both records are about one chat, "
                        f"so one of them is wrong about what it is, and which "
                        f"one is not this reader's to decide"),
            })
    return out


def _planes(records_root: Path) -> dict:
    """The two registries this module reads and does not merge.

    `docs/DEEP-ARCHIVE-POLICY.md` §8 keeps the publish-plane criteria apart from
    the volume-plane ones and says 两本判据不合并、不另建重复登记册. They sit in
    the same directory as the exclusions, which is exactly why a reader would
    fold them in; this reports their presence and names why they are not inputs.
    """
    information = Path(records_root).joinpath("information")
    planes = {}
    for name, filename, why in (
        ("heavy_media", "heavy-media-externalized.yaml",
         "volume and storage policy: which bytes live outside Git because of "
         "their size. A size decision is not a collection decision"),
        ("publish_excluded", "publish-excluded.json",
         "release-surface policy: which content is not fit for this repository's "
         "audience. Content can be in scope for collection and out of scope for "
         "release, in either direction"),
        ("credential_registry", "credential-registry",
         "the credential plane, which is its own registry and whose whole "
         "subject is one this module never reads a value from"),
    ):
        path = information / filename
        planes[name] = {
            "path": str(path),
            "exists": path.exists(),
            "why_not_a_policy_input": why,
        }
    return planes


def _summarize(rows: list[dict], objects: int, decl: dict, matched: dict) -> dict:
    by_state: dict = {}
    by_policy: dict = {}
    by_decided_by: dict = {}
    for row in rows:
        by_state[row["state"]] = by_state.get(row["state"], 0) + 1
        for label in row["policies"]:
            by_policy[label] = by_policy.get(label, 0) + 1
        if row["decided_by"]:
            by_decided_by[row["decided_by"]] = by_decided_by.get(row["decided_by"], 0) + 1
    unusable = list(decl["unusable"]) + list(matched["unusable"])
    return no_unsourced({
        "objects": objects,
        "rows": len(rows),
        "by_state": by_state,
        "by_policy": by_policy,
        "by_decided_by": by_decided_by,
        "unusable_declarations": {
            "count": len(unusable),
            "items": unusable,
            "why": ("" if not unusable else
                    "each of these entries is a rule a person wrote that this "
                    "reader could not key or could not read. They are counted "
                    "because every one of them is an object that may be missing "
                    "from the rows above, and a policy report whose denominator "
                    "silently shrank is the failure mode this count exists for"),
        },
        "near_misses": matched["near_misses"],
    }, SUMMARY_PRODUCED, "summary")


def _row(object_row: dict, rules: list[dict], decl: dict,
         legacy_deny: dict) -> dict:
    """One capture object's row: the labels in force and the rule that decided."""
    identity = object_row["identity"]
    why: list[str] = []
    if identity.get("why"):
        why.append(identity["why"])
    state = "IN_FORCE"
    policies: list[str] = []
    policy: str | None = None
    axis: str | None = None
    decided_by: str | None = None
    bypassed: list[dict] = []
    conflict: dict | None = None
    declared: dict = {}
    follow: dict = {"state": "NOT_DECLARED", "why": ""}
    reference: dict = {"state": "NOT_DECLARED", "why": ""}

    # A rule that puts nothing in force is CONTEXT and not a decision: the
    # registry's `reclassified` block clears a deny and decides nothing, so it
    # is reported in `why` and does not stop the object from reaching the level
    # that does decide it.
    for rule in rules:
        if not rule["labels"]:
            why.append(rule["why"])
    decisions = [rule for rule in rules if rule["labels"]]
    levels: dict[str, list[dict]] = {}
    for rule in decisions:
        levels.setdefault(rule["level"], []).append(rule)
    reached = next((level for level in PRECEDENCE if level in levels), None)

    if reached is None:
        if identity.get("kind") != "chat" or identity.get("key") is None:
            state = "UNDECLARED"
            if identity.get("kind") == "chat":
                why.append(
                    "no rule reaches this object and it has no stable identity "
                    "for one to be written against: a manifest with no "
                    "`chat.chat_id` cannot be named by a declaration, so it "
                    "takes no default")
            else:
                why.append(
                    "no rule reaches this object. It is a document captured "
                    "from a chat, which makes it material OF that chat; whether "
                    "it is also a followed source is a decision nobody has "
                    "made, and the general default for chats is not a default "
                    "for documents")
        else:
            policies = ["SHALLOW"]
            policy, axis = "SHALLOW", POLICY_AXES["SHALLOW"]
            decided_by = "GENERAL_DEFAULT"
            why.append(
                "no rule reaches this object: it is a chat with an identity "
                "and no declaration, no accepted relation and no legacy line, "
                "so it takes the general default of a shallow archive. The "
                "default is stated with the rule that produced it because a "
                "default that cannot be told from a decision is how a policy "
                "nobody made gets obeyed")
            denominator = legacy_deny.get("denominator")
            if denominator:
                why.append(
                    f"the exclusion registry closed over "
                    f"{denominator['excluded']} objects and this is not one of "
                    f"them, so its silence here is the registry's own and not "
                    f"an unread file's")
    else:
        level_rules = levels[reached]
        labels: list[str] = []
        for rule in level_rules:
            for label in rule["labels"]:
                if label not in labels:
                    labels.append(label)
        decided_by = level_rules[0]["decided_by"] if len(level_rules) == 1 else None
        # The conflict is asked of the labels WITHOUT the deny: `NONE` is not a
        # second answer to the depth question, it is the order not to ask it, so
        # a deny beside a depth label is not the level contradicting itself.
        conflict = _axis_conflict([l for l in labels if l != "NONE"])
        if conflict:
            state = "CONFLICT"
            why.append(conflict["why"])
        if "NONE" in labels:
            # A deny is in force and nothing below it gets to bypass it.
            policies = ["NONE"]
            policy, axis = "NONE", POLICY_AXES["NONE"]
            for rule in decisions:
                if PRECEDENCE.index(rule["level"]) <= PRECEDENCE.index(reached):
                    continue
                for label in rule["labels"]:
                    if label == "NONE":
                        continue
                    bypassed.append({
                        "labels": [label], "decided_by": rule["decided_by"],
                        "why": ("a label met below a deny. The entry that wrote "
                                "it is still on disk and its author still "
                                "expects it to mean something, so it is "
                                "reported here rather than dropped"),
                    })
        elif conflict:
            policies = sorted(labels)
        else:
            policies = sorted(labels)
            policy, axis = _decide(labels)
        for rule in level_rules:
            why.append(rule["why"])
        declared = (level_rules[0]["evidence"].get("declared") or {})
        follow = _block_of(decl, identity, "follow", FOLLOW_PRODUCED,
                           "FOLLOW", level_rules)
        reference = _block_of(decl, identity, "reference", REFERENCE_PRODUCED,
                              "REFERENCE", level_rules)

    if object_row["shape"] not in ("chat_capture", "document_capture"):
        state = "UNREADABLE"
        why.append(f"this object's shape is `{object_row['shape']}`, so no "
                   f"manifest was parsed and no identity could be matched "
                   f"against a declaration")

    return no_unsourced({
        "object": object_row["object"],
        "role": object_row["role"],
        "shape": object_row["shape"],
        "identity": identity,
        "observed_as": identity.get("observed_as"),
        "label": identity.get("label"),
        "state": state,
        "policies": policies,
        "policy": policy,
        "axis": axis,
        "axes": sorted({POLICY_AXES[label] for label in policies}),
        "decided_by": decided_by,
        "why": why,
        "rules": rules,
        "bypassed": bypassed,
        "declared": declared,
        "follow": follow,
        "reference": reference,
        "migration": _migration(rules),
        "same_identity_as": [],
        "coverage_owner": _coverage_owner(object_row["shape"]),
    }, ROW_PRODUCED, "row")


def _block_of(decl: dict, identity: dict, name: str, table: dict, label: str,
              level_rules: list[dict]) -> dict:
    """The follow/reference block a declaration carries for this object."""
    for entry in decl["entries"]:
        ok, _ = _identity_match(entry["identity"], identity)
        if not ok:
            continue
        block = entry.get(name)
        if isinstance(block, dict):
            missing = sorted(set(table) - set(block))
            return {"state": "DECLARED" if not missing else "INCOMPLETE",
                    "missing": missing,
                    "block": {k: block[k] for k in sorted(block)},
                    "why": "" if not missing else
                           f"declared with {', '.join(missing)} missing"}
    if label in {l for rule in level_rules for l in rule["labels"]}:
        return {"state": "UNDECLARED_BY_LEGACY", "missing": sorted(table),
                "block": {},
                "why": (f"{label} is in force from a legacy line, which carries "
                        f"no block: the label is a real instruction to the "
                        f"collector and the eight declarations a follow needs "
                        f"are exactly what the migration to "
                        f"information/sources/ has to supply")}
    return {"state": "NOT_DECLARED", "missing": [], "block": {}, "why": ""}


def _migration(rules: list[dict]) -> dict:
    """What this row still needs before it is a declaration the design names."""
    legacy = [rule for rule in rules if rule["decided_by"].startswith("LEGACY")]
    if not legacy:
        return {"needed": False, "from": [], "to": "", "why": ""}
    return {
        "needed": True,
        "from": sorted({rule["decided_by"] for rule in legacy}),
        "to": "information/sources/<file>.yaml",
        "why": (f"this object's policy comes from a list the design migrates "
                f"away from. Until a declaration carries it, the list is the "
                f"only record of it, and §6's 不让两份可编辑列表同时生效 is a "
                f"statement about what happens the day the two disagree"),
    }


def _coverage_owner(shape) -> str:
    return {
        "chat_capture": "intake/capture_coverage.py — messages, attachments, links",
        "document_capture": "intake/capture_coverage.py — body, media",
    }.get(shape, "none: no capture reader parsed this object")


def _unresolved_row(key: str, decided_by: str, why: str) -> dict:
    """A declaration or legacy line naming an object no capture object holds."""
    identity = {"kind": None, "provider": None, "key": key, "chat_id": None,
                "url": None, "open_id": None, "tenant_key": None,
                "chat_type": None, "label": None, "observed_as": None,
                "why": why}
    return no_unsourced({
        "object": key,
        "role": "UNKNOWN_ROLE",
        "shape": "NO_CAPTURE_OBJECT",
        "identity": identity,
        "observed_as": None,
        "label": None,
        "state": "UNRESOLVED",
        "policies": [],
        "policy": None,
        "axis": None,
        "axes": [],
        "decided_by": decided_by,
        "why": [why],
        "rules": [],
        "bypassed": [],
        "declared": {},
        "follow": {"state": "NOT_DECLARED", "missing": [], "block": {}, "why": ""},
        "reference": {"state": "NOT_DECLARED", "missing": [], "block": {}, "why": ""},
        "migration": {"needed": False, "from": [], "to": "", "why": ""},
        "same_identity_as": [],
        "coverage_owner": "none: no capture object holds this source",
    }, ROW_PRODUCED, "row")


def project_rows(document: dict, fields: list[str]) -> dict:
    """The resolved rows in the shape `workchain.source_coverage` reads.

    That function is the existing consumer of a followed-source list, and it
    reads `records/projects/<project>/context/sources.yaml` — a file nobody has
    written, which is why every project reports UNPRODUCED there today. This is
    the adapter that lets the resolved policy answer it instead, and it is
    deliberately only an adapter: the fields it must supply come from the
    consumer's own contract entry rather than being restated here, so a field
    the consumer adds shows up as MISSING rather than as a field this module
    never knew about.
    """
    rows = []
    for row in document["rows"]:
        # What is handed over is a FOLLOWED SOURCE LIST, so a capture object
        # with no label in force is not on it: a document nobody has decided
        # about is not a source somebody follows, and emitting it would inflate
        # the consumer's `authored` with objects nobody authored anything about.
        # It is still visible — as UNDECLARED on its own row — which is where a
        # reader is looking when the question is what still needs a decision.
        if row["state"] in ("UNRESOLVED", "UNREADABLE") or not row["policies"]:
            continue
        follow = row["follow"]
        row_out = {
            "surface": "serverosctl",
            "identity": row["identity"].get("key"),
            "because": next((rule["why"] for rule in row["rules"]), row["why"][0]
                            if row["why"] else ""),
            "refresh": (follow.get("block") or {}).get("check_policy"),
            "depth": row["policy"],
            "credential_ref": (row["declared"] or {}).get("credential_ref"),
            "publishable": (row["declared"] or {}).get("publishable"),
        }
        missing = [field for field in fields if row_out.get(field) in (None, "")]
        rows.append({**row_out, "missing_fields": missing,
                     "object": row["object"],
                     "state": "PRESENT" if not missing else "MISSING"})
    return {"state": "READ" if rows else "UNPRODUCED",
            "fields": list(fields), "rows": rows,
            "why": "" if rows else
                   "no capture object carries a policy, so there is no followed "
                   "source list to hand a consumer"}


def render(document: dict) -> list[str]:
    """The policy as lines: what is in force, then what is not, then the gaps."""
    lines = [f"root        {document['root']}"]
    decl = document["declarations"]
    lines.append(f"declarations {decl['state']}  {decl['path']}")
    if decl["why"]:
        lines.append(f"    · {decl['why']}")
    for item in decl["unusable"]:
        lines.append(f"    ! {item.get('id') or '(no id)'}：{item['why']}")
    legacy = document["legacy"]
    lines.append(f"watchlist   {legacy['watchlist']['state']}  "
                 f"{', '.join(legacy['watchlist']['keys']) or '—'}")
    lines.append(f"exclusions  {legacy['not_archived']['state']}  "
                 f"{len(legacy['not_archived']['denied'])} denied · "
                 f"{len(legacy['not_archived']['reclassified'])} reclassified")
    summary = document["summary"]
    lines.append("")
    lines.append(f"objects     {summary['objects']} · 行 {summary['rows']} · "
                 + " · ".join(f"{k} {v}" for k, v in sorted(summary["by_state"].items())))
    if summary["by_decided_by"]:
        lines.append("decided_by  " + " · ".join(
            f"{k} {v}" for k, v in sorted(summary["by_decided_by"].items())))
    lines.append("")
    for row in document["rows"]:
        label = row["label"] or row["observed_as"] or row["object"]
        identity = (row["identity"] or {}).get("key") or "（无 identity）"
        lines.append(f"{label}  {row['state']}  "
                     f"{'/'.join(row['policies']) or '—'}  {identity}")
        lines.append(f"    object     {row['object']}")
        if row["bypassed"]:
            for item in row["bypassed"]:
                lines.append(f"    bypassed   {'/'.join(item['labels'])} ← "
                             f"{item['decided_by']}：在 deny 之下不在效力")
        if row["follow"]["state"] != "NOT_DECLARED":
            lines.append(f"    follow     {row['follow']['state']}"
                         + (f" 缺 {', '.join(row['follow']['missing'])}"
                            if row["follow"]["missing"] else ""))
        if row["reference"]["state"] != "NOT_DECLARED":
            lines.append(f"    reference  {row['reference']['state']}")
        if row["same_identity_as"]:
            lines.append(f"    同一身份   还有 {len(row['same_identity_as'])} 条发现边："
                         + "、".join(row["same_identity_as"]))
        if row["migration"]["needed"]:
            lines.append(f"    迁移       {', '.join(row['migration']['from'])} → "
                         f"{row['migration']['to']}")
        lines.append(f"    coverage   {row['coverage_owner']}")
        for sentence in row["why"]:
            lines.append(f"    · {sentence}")
    if summary["unusable_declarations"]["count"]:
        lines.append("")
        lines.append(f"不可用的声明 {summary['unusable_declarations']['count']}："
                     f"{summary['unusable_declarations']['why']}")
        for item in summary["unusable_declarations"]["items"]:
            lines.append(f"    ! {item.get('id') or '(no id)'}：{item['why']}")
    if summary["near_misses"]:
        lines.append("")
        for item in summary["near_misses"]:
            lines.append(f"近似 {item['entry']} ↔ {item['object']}：{item['why']}")
    return lines


# The producer tables, declared after the code that reads them so that every key
# above is checked against a name rather than against a comment.
DECLARATIONS_PRODUCED = {
    "state": "ABSENT | EMPTY | PARTIAL | READ — the location's own state",
    "path": "the location read",
    "files": "the declaration files found, relative to the location",
    "entries": "the source declarations that could be keyed",
    "contacts": "the contact seeds that could be keyed",
    "unusable": "entries that could not be keyed, with the reason",
    "unreadable": "files that could not be parsed, with the reason",
    "why": "the sentence a reader needs when the location is not simply READ",
}

WATCHLIST_PRODUCED = {
    "state": "ABSENT | UNREADABLE | READ",
    "path": "the file read",
    "keys": "the un-commented lines, as written (slugs, not identities)",
    "why": "why the list is empty or unread, when it is",
}

NOT_ARCHIVED_PRODUCED = {
    "state": "ABSENT | UNREADABLE | READ",
    "path": "the file read",
    "denied": "the registered exclusions, keyed by the chat id they name",
    "reclassified": "the entries a person moved OUT of this registry, kept so "
                    "that reading the registry does not resurrect the judgement "
                    "that was overruled",
    "denominator": "the registry's own count, which is what its silence is over",
    "why": "why the registry is empty or unread, when it is",
}

DOCUMENT_PRODUCED = {
    "schema": "SCHEMA",
    "root": "the records root this was read from",
    "declarations": "the desired declarations",
    "legacy": "the two lists being migrated, each with its state",
    "planes": "the registries read and not merged, each with why",
    "contradictions": "pairs of records about one object that cannot both be "
                      "right",
    "rows": "one row per capture object, plus one per unresolved rule",
    "summary": "the counts, and the entries that could not be keyed",
}
