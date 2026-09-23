"""The materials a capture refers to, and the four coverages over them.

WHY THIS MODULE EXISTS
----------------------
`docs/DEEP-ARCHIVE-POLICY.md` §4 makes one statement a per-object status field
cannot carry: **一条消息N个材料，同一材料可有多消息来源** — one message has N
materials, one material can have several message sources. §5 then refuses the
collapse that follows from getting it wrong: message, discovery, material and
understanding coverage are four questions with four denominators, and
"0 材料且 parser 未知" is `PARTIAL`, never `0/0 = 100%`.

`records/information/raw/` says the collapse is not hypothetical. Measured
read-only over the 47 chat captures on 2026-09-23:

* 1,181 message rows carry a file token in their own content, and **110 of them
  carry more than one** — a single `post` names five.
* `files.by_message` — the legacy single-key map — holds 63 entries where 112
  token-named files sit on disk. A reader that took the map for the material
  denominator would understate this capture by 44% and would never see the other
  49. §4 calls the map **navigation only**, and that is what it is here.
* the message contents name **228 distinct URLs**; the collector expanded **19**.
  The `links/` directory is the collector's account of its own work, not the
  denominator of what the messages point at.
* 494 messages are `nonsupport`, 427 `image` rows name no token at all, and
  `Nature.Hu`'s manifest declares four backfilled names over three files on
  disk, while `Bruce.Xu`'s declares `pending: "3 张图片未下载"` over three files
  that ARE on disk. The two are opposite defects and only the pair of
  `landing` and `registry` states tells them apart.

WHAT IT REFUSES TO DO
---------------------
It does not let any single field be the completeness authority. Each material
carries `discovery` (which forms found it, in which messages), `landing` (where
the bytes are) and `registry` (what the manifests say) as three separate facts,
and the **finding is the pair** — registered-but-absent and present-but-declared
-pending are different problems with different fixes, one of which is "do not
download it again".

It does not treat a message row that yielded nothing as a message with nothing.
A row is `PLACEHOLDER` when the collector wrote its own bracketed label instead
of the material, `EMPTY_IN_A_MATERIAL_TYPE` when a type that carries material
yielded no reference at all, and `UNSUPPORTED_TYPE` when the collector recorded
that it could not render the message. All three keep discovery at `PARTIAL`
with their counts. A pattern that misses a whole token family shows up as a gap
here rather than as silence — that is the reason the message types are declared
in this module instead of being inferred from what the patterns happen to find.

It does not read a message body into anything it returns. The rows are parsed
for `message_id`, `message_position`, `msg_type`, the revision flags, and the
matches of the declared reference forms; `content` is scanned and dropped, so no
returned value can carry text.

It does not decide how deep a source must go — `docs/DEEP-ARCHIVE-POLICY.md` §1
gives that to the source policy. `policies` is the resolver's own row for this
source, copied, and it is what decides whether an unlanded material is a gap or
a locator that is allowed to stay one. With no policy row the material half is
`UNMEASURABLE` and says so, rather than assuming the strict reading.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCHEMA = "serveros/material-index.v1"

# The records-repo layout, named once. Not declared roots in the root contract,
# so the caller supplies the records root and these segments are appended —
# the same two constants `intake/capture_coverage.py` composes its own root
# from, kept as literals here because a reader's key space must not depend on
# another module's internals.
RAW_SEGMENTS = ("information", "raw")
CAPTURE_SEGMENTS = ("feishu", "chats")

#: The file a capture writes its messages to, beside the manifest. Composed
#: here from the object's own directory; `tests/test_material_index.py` asserts
#: this agrees with `understanding_delta.messages_file` for every object on the
#: live tree, so the two derivations cannot drift apart unnoticed.
MESSAGES_NAME = "messages.jsonl"
MANIFEST_NAME = "manifest.json"
FILES_DIR = "files"

#: The three identity spaces a material can be keyed in. `doc` is deliberately
#: `source_policy.key_of`'s document space and not a new one: a document
#: referenced from a message and expanded by the collector is ONE material with
#: two discovery edges (§9 of DEEP-ARCHIVE-POLICY), and it would be two if this
#: module invented a second doc key.
KEY_SPACES = ("material", "doc")


def _token_kind(match: re.Match) -> str:
    """`image` or `file`, from the token family the collector wrote."""
    return "image" if match.group(1).startswith("img_") else "file"


#: Every way a message body can name a material, and the message types each
#: form is DECLARED to examine. A material-bearing type that no form claims is
#: a `NO_PARSER` gap — declared, not discovered, so a form that silently stops
#: matching turns rows into gaps instead of into "this message had no material".
REFERENCE_FORMS = {
    "file-token": {
        "pattern": re.compile(r"\b((?:img|file)_v\d+_[0-9a-z]{2,}_[0-9a-f-]{8,}g?)\b"),
        "kind": _token_kind,
        "id_space": "material",
        "what": "the collector's own rendering of a Feishu attachment: the file "
                "token, written into the message content",
        "declared_for": ("image", "file", "media", "post", "merge_forward",
                         "interactive"),
    },
    "html-file-key": {
        "pattern": re.compile(r'file_key="([^"]{8,})"'),
        "kind": lambda match: "attachment",
        "id_space": "material",
        "what": "the attribute the collector wrote for a card-shaped "
                "attachment. It usually wraps a token `file-token` also finds, "
                "and when both match one span the token wins and this form is "
                "recorded on the same edge as `also_matched_by`",
        "declared_for": ("file", "media", "interactive"),
    },
    "markdown-link": {
        "pattern": re.compile(r"\[[^\]\n]{0,200}\]\((https?://[^)\s]+)\)"),
        "kind": lambda match: "web",
        "id_space": "doc",
        "what": "a URL the body points at. This is the discovery edge for an "
                "external reference (§EXTERNAL-REFERENCE-LIBRARY): a document "
                "reached from a chat and separately Followed is one source with "
                "several edges, and the collector's own expansion is one of them",
        "declared_for": ("post", "interactive", "text", "merge_forward"),
    },
}

#: Which form owns a span two forms both matched. First wins. `file-token` is
#: above `html-file-key` because the token is what the collector wrote and the
#: attribute is a rendering of it, and a form that agreed is recorded on the
#: winner's edge rather than dropped. A form missing from this tuple would make
#: `index` raise rather than silently sort last, which is why the suite asserts
#: the tuple and `REFERENCE_FORMS` name the same three forms.
FORM_PRECEDENCE = ("file-token", "html-file-key", "markdown-link")

#: The capture pipeline replaces what it may not keep — a share link's token,
#: a credential in a URL — with a marker of this shape, and the marker is all
#: that reaches this module. Two different links replaced the same way are
#: indistinguishable here, so a redacted value is an OCCURRENCE and never an
#: identity: measured on this tree, 24 references carry a marker over 9
#: distinct values, and one calendar share link's marker stands for several
#: different shares. Keying them as one material would report "one material,
#: several sources" where the truth is "several materials, no identities".
REDACTION_MARKER = re.compile(r"<redacted-[a-z-]+>")


def identity_established(value) -> bool:
    """Whether a reference value can name a material at all."""
    return not REDACTION_MARKER.search(str(value))

#: The message types whose own content is expected to name material. A row of
#: one of these types that yields no reference and no placeholder is a gap, not
#: an absence — §3 of DEEP-ARCHIVE-POLICY: 没有解析到不能等于没有材料.
MATERIAL_TYPES = ("image", "file", "media", "post", "merge_forward",
                  "interactive", "video_chat")
#: Types the collector recorded it could not render. Every row is a gap by the
#: collector's own admission, which is the strongest form this tree carries.
OPAQUE_TYPES = ("nonsupport",)
#: Types whose content is a label the collector wrote and nothing else. They
#: are not material-bearing; their rows are not gaps.
LABEL_ONLY_TYPES = ("sticker", "vote", "todo", "share_calendar_event",
                    "share_chat", "system")

#: What one message row amounts to, decided by evidence and in this order.
EDGE_STATES = ("UNSUPPORTED_TYPE", "PLACEHOLDER", "EMPTY_IN_A_MATERIAL_TYPE",
               "REFERENCES", "NO_REFERENCE_EXPECTED")

#: Worst first. `NONE_MEASURED` is not a flavour of `COMPLETE`: a source with no
#: message rows has no denominator, and §5 forbids reading that as 100%.
DISCOVERY_STATES = ("UNREADABLE", "NONE_MEASURED", "PARTIAL", "COMPLETE",
                    "NOT_APPLICABLE")

#: Where the bytes are. Independent of what the manifests say about them.
LANDING_STATES = ("LANDED", "ABSENT", "UNKNOWN")

#: What the manifests say. A material may be in several of these at once, and
#: `DECLARED_PENDING` beside `LANDED` is the stale marker §10 warns about.
REGISTRY_STATES = ("DECLARED_BACKFILLED", "DECLARED_PENDING",
                   "IN_LEGACY_BY_MESSAGE", "IN_LINK_MEDIA")

#: Worst first. `NONE_REQUIRED_BY_POLICY` is not a pass — it is the policy
#: saying the closure was never an obligation, and it names the row it read.
MATERIAL_STATES = ("UNMEASURABLE", "PARTIAL", "COMPLETE",
                   "NONE_REQUIRED_BY_POLICY")

#: The understanding half is measured elsewhere or not at all. This module
#: reads materials; it does not read cards, and a second reader of the cards
#: would be a second answer to a question that already has one.
#: What this module says when it did not look. The measured states are NOT
#: enumerated here on purpose: they are `source_waterline`'s `COMPARISON_STATES`
#: copied verbatim, and a second copy of a vocabulary is a second place to
#: forget to add a state. The suite asserts the containment against that module,
#: which is where the vocabulary lives.
UNDERSTANDING_NOT_MEASURED = "NOT_MEASURED"

# ---------------------------------------------------------------- producers --
#: Every key a discovery edge may carry, and where its value comes from. The
#: guard at emission refuses an orphan key: a field is added here before it can
#: leave this module.
REFERENCE_PRODUCED = {
    "producer": "MESSAGE_CONTENT when the reference was read out of a message "
                "body, LINK_OBJECT when it is an expansion the collector made. "
                "Two producers and neither is a count of the other: one is what "
                "was said and one is what was fetched, and on this tree the two "
                "numbers differ by an order of magnitude",
    "source": "the capture object this edge was found in — the coordinate a "
              "material's `sources` is built from",
    "snapshot": "the collector's own record of what it fetched — content "
                "digest, fetch time, title. Present on a link-object edge and "
                "null on a message edge, because a message names a material "
                "and does not vouch for its bytes",
    "form": "the REFERENCE_FORMS entry whose pattern matched, or `link-object` "
            "and `link-media` for the edges only the collector's own expansion "
            "can produce",
    "kind": "that entry's `kind` — image | file | attachment | web — applied to "
            "the match",
    "id_space": "that entry's `id_space` — which identity space the value is "
                "keyed in, so a reader never has to guess",
    "value": "the matched token or URL, verbatim",
    "part": "`<form>#<n>` — the occurrence of this match within the row, which "
            "is what tells two images in one `post` apart",
    "also_matched_by": "other forms that matched a span inside this one. The "
                       "edge is one, the evidence is named",
    "identity_established": "false when the value carries the capture "
                            "pipeline's own redaction marker. Such a value is "
                            "not an address: two different links replaced by "
                            "the same marker must not be keyed as one material, "
                            "and the edge says so rather than showing an id it "
                            "does not have",
    "message_id": "the row's `message_id`",
    "message_position": "the row's `message_position`",
    "msg_type": "the row's `msg_type`",
    "create_time": "the row's `create_time`",
    "sender_type": "`sender.sender_type` — kept because a message from an app "
                   "is not a message from the contact whose archive this is",
    "parent_forward_id": "the `merge_forward` row this reference was found "
                         "inside, or null. The forwarded sub-message has no id "
                         "of its own in this record, and §3 says to associate "
                         "weakly rather than to invent one",
    "association": "STRONG when the row is the message that carries the "
                   "reference, WEAK when it was flattened into a forward "
                   "container. Never inferred from the text",
    "revision": "the row's own revision flags — `updated`/`edited` and "
                "`deleted` — copied, because a material referenced only from a "
                "deleted row is not the same finding as one referenced from a "
                "live row",
}

MATERIAL_PRODUCED = {
    "material_id": "this module's stable key: `<provider>:material:<scope>:"
                   "<token>` or `<provider>:doc:<url>`",
    "provider": "the first path segment under the raw root — read from the "
                "tree, not assumed, and `feishu` on this node",
    "key_space": "one of KEY_SPACES, so the id can be read without a pattern",
    "kind": "image | file | attachment | web",
    "token": "the provider token, when there is one. Recorded ALONGSIDE the id "
             "and never as the id: §4 says a token is neither globally unique "
             "nor guaranteed to stay the same",
    "url": "the URL, for a web or document material",
    "identity_state": "ESTABLISHED, or REDACTED when the only name this "
                      "material was ever given carries the capture pipeline's "
                      "redaction marker. A REDACTED material is a real finding "
                      "— a message named something — and it can never land or "
                      "be read back, so it is reported as an unclosable gap "
                      "instead of as a locator",
    "scope": "the capture object this material was keyed within, from its "
             "`chat_id` or its directory. Cross-scope token reuse is reported "
             "and never merged, because bytes shared across scopes is exactly "
             "the inference §4 forbids making from a token or a hash",
    "scope_source": "which rule produced the scope — `chat_id`, "
                    "`directory_name`, `link_directory`, or `redacted_identity` "
                    "when the value could not be an identity and the occurrence "
                    "became the scope. A reader can see when a "
                    "display-name-shaped directory was all there was, and when "
                    "the scope is the only thing keeping two materials apart",
    "sources": "the capture objects whose evidence found it, sorted. More than "
               "one is the normal case §4 describes",
    "edges": "how many discovery edges point at it — the sum over messages",
    "messages": "how many distinct messages reference it",
    "senders": "the distinct `sender_type` values across those edges",
    "message_time": "the earliest and latest `create_time` among the edges",
    "discovery_forms": "the forms that found it, sorted",
    "landing": "LANDING_PRODUCED, below",
    "registry": "REGISTRY_PRODUCED, below",
    "snapshots": "the link objects that fetched this material, each with its "
                 "digest and fetch time, copied from the edges. Reported as "
                 "evidence of a fetch and never as the material's own hash",
    "declared_bytes": "the size a manifest declared for it, or null",
    "bytes_agree": "true when a declared size and a measured size are both "
                   "present and equal, false when both are present and differ, "
                   "null when one is missing — a comparison that could not be "
                   "made, never a pass",
    "mime": "the `media_kind` a manifest recorded, or the declared name's "
            "suffix, or null. Two sources, and the one used is named",
    "mime_source": "`media_kind` | `name_suffix` | null",
    "name": "the declared file name, or null",
    "locator": "where the bytes are, as records-root-relative text, or null. "
               "Never an absolute path: a report that carries this host's "
               "layout is not portable evidence",
    "time": "the earliest `downloaded_at` a manifest recorded for it, or null",
    "permission": "what a manifest recorded about permission, or an explicit "
                  "NOT_RECORDED. §5: a permission refusal ends an attempt; it "
                  "does not make material absent, and it is not a success",
    "retention": "what a manifest recorded about retention, or NOT_RECORDED",
    "redaction": "the redaction a manifest declares over this object, copied, "
                 "when the evidence carries one",
    "why": "every reason this material is not fully evidenced, in words",
}

LANDING_PRODUCED = {
    "state": "one of LANDING_STATES, decided by what is on the disk",
    "bytes": "the measured size, or null when nothing is there",
    "sha256": "the measured digest, when the caller asked for it",
    "hash_state": "MEASURED | NOT_MEASURED — the second carries its reason, "
                  "because a landing that was never hashed cannot be compared "
                  "to a registered digest and must not read as one that was",
    "why": "one sentence per fact about where the bytes are",
}

REGISTRY_PRODUCED = {
    "states": "the REGISTRY_STATES this material is in, sorted",
    "name_on_disk": "the file name the bytes were found under",
    "names_registered": "the names the manifests used for it. A token is "
                        "registered WITHOUT its suffix and lands WITH it, so "
                        "this is where the two spellings are visible instead of "
                        "silently failing to join",
    "declared_bytes": "the size the manifest recorded, or null",
    "pending_text": "the collector's own `files.pending` sentence, verbatim, "
                    "when it is the only thing that speaks for this material. "
                    "Reported as prose and never parsed into a count",
    "why": "one sentence per fact about what the manifests claim",
}

COVERAGE_PRODUCED = {
    "message": "COVERAGE_HALF, below — the collector's own account, copied",
    "discovery": "COVERAGE_HALF, below — this module's count over the rows",
    "material": "COVERAGE_HALF, below — landing against the policy",
    "understanding": "COVERAGE_HALF, below — copied from the caller's "
                     "measurement, or NOT_MEASURED",
}

COVERAGE_HALF_PRODUCED = {
    "state": "one of this half's own STATES tuple",
    "measured_by": "the module that measured it. For `message` it is "
                   "`intake/capture_coverage.py`; for `understanding` it is "
                   "whatever the caller passed. A half is never re-derived "
                   "here and never silently attributed to this module",
    "facts": "the counts this half was decided from, each key named in a "
             "producer table",
    "gaps": "the rows that keep this half off COMPLETE, each with the evidence "
            "that made it a gap",
    "refusal": "present when the half could not be decided, carrying what would "
               "have to be true for it to be — the shape §5 asks for instead of "
               "a ratio over an absent denominator",
    "why": "one sentence per reason the state is not COMPLETE",
}

OBJECT_PRODUCED = {
    "key": "the source identity in `source_policy.key_of`'s shape, so the two "
           "modules' rows can be joined and a disagreement is visible",
    "identity": "the object's own name/url as the manifest wrote it",
    "path": "the manifest path relative to the raw root — this module's "
            "coordinate, copied from the capture object it was handed",
    "shape": "the capture object's shape, copied from capture_coverage",
    "role": "the capture object's role, copied from capture_coverage",
    "scope": "the scope this object's materials are keyed within",
    "state": "READ, or UNREADABLE when this module could not read the "
             "manifest that `capture_coverage` counted. A row that keeps its "
             "place in the list and says it was not read",
    "coverage": "COVERAGE_PRODUCED, below",
    "rows": "how many message rows were read for this object",
    "materials": "the ids of the materials this object's evidence found — "
                 "including a material whose only evidence is a manifest list, "
                 "because the list is evidence and calling it nothing would "
                 "delete the finding",
    "materials_this_scope": "the same, sorted — kept separately so a consumer "
                            "joining on the id can see which of the materials "
                            "this object reached",
    "legacy": "what the legacy single-key map says, and what it cannot say",
    "stale_markers": "registry entries the measured bytes contradict — the "
                     "§10 row that says check before downloading again",
    "pending_claims": "the manifest's `files.pending` sentence, with what it "
                      "names and what is on disk beside it. A sentence that "
                      "names no token is a claim with no subject, and it is "
                      "reported as that instead of being parsed into a count",
    "unindexed": "bytes on disk no manifest list names, each with its size",
    "why": "one sentence per fact about this object",
}

DOCUMENT_PRODUCED = {
    "schema": "this module's own schema string",
    "root": "the records root these materials were read against",
    "objects": "one row per capture object, from the objects the caller handed "
               "in — the same list `capture_coverage` counted, so the "
               "denominator is one number and not two",
    "materials": "every material, keyed by its id. One dict rather than a list "
                 "per object, because §4's whole point is that a material has "
                 "several sources and an object-scoped list cannot say so",
    "collisions": "messages where the legacy map's answer and the measured "
                  "edges disagree. §4 says not to silently pick the last "
                  "attachment for a consumer, so each one is named",
    "policy": "POLICY_JOIN_PRODUCED, below — how the policy document joined "
              "against these objects, so `UNMEASURABLE` everywhere can be told "
              "apart from `NOT_PASSED`",
    "summary": "counts, per axis and never collapsed",
}

POLICY_JOIN_PRODUCED = {
    "state": "READ, or NOT_PASSED when no policy document was handed in",
    "rows": "the policy rows this reading was handed",
    "joined": "how many of them name an object this reading also reached",
    "unjoined": "up to 50 policy keys no object of this reading matches",
    "unjoined_total": "how many those are, since the list above is capped",
    "why": "one sentence when the join is short of the whole",
}


class Refused(RuntimeError):
    """This reader will not answer. Raised for a defect in the CALLER."""


class Malformed(Refused):
    """One file is not the record it claims to be. A fact about the file."""


def no_unsourced(mapping: dict, table: dict, where: str) -> dict:
    """Refuse to emit a key the producer table does not name a source for."""
    unexpected = sorted(set(mapping) - set(table))
    if unexpected:
        raise Refused(
            f"this module would emit "
            f"{', '.join(f'`{where}.{key}`' for key in unexpected)}, which the "
            f"producer table does not name a source for; add the producer or "
            f"drop the field")
    return mapping


def _worst(states, order: tuple) -> str:
    """The worst of `states`, by the order the caller declares."""
    return min(states, key=order.index)


def _number(value):
    """An int, or `None` when the record carries something else.

    A string count would compare unequal to an int and produce a false
    disagreement, so a non-int is absent and the half reports it as such.
    """
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def raw_root(records_root: Path) -> Path:
    """`<records_root>/information/raw` — composed here so callers cannot differ."""
    return Path(records_root).joinpath(*RAW_SEGMENTS)


def capture_root(records_root: Path) -> Path:
    """`<records_root>/information/raw/feishu/chats` — where the collectors write."""
    return raw_root(records_root).joinpath(*CAPTURE_SEGMENTS)


def messages_path(directory: Path) -> Path:
    """The messages file beside a capture object's manifest."""
    return Path(directory) / MESSAGES_NAME


def files_dir(directory: Path) -> Path:
    """Where a capture's landed bytes live, beside its manifest."""
    return Path(directory) / FILES_DIR


def provider_of(relative_path: str) -> str:
    """The provider, from the first segment of the object's own path.

    Read from the tree rather than written as a literal: the id spaces of every
    material start with it, and a second provider landing under `raw/` must not
    have to be added to a constant before its materials can be keyed.
    """
    parts = [part for part in str(relative_path).split("/") if part]
    return parts[0] if parts else "UNKNOWN"


# ------------------------------------------------------------- the messages --
def _nests(a: dict, b: dict) -> bool:
    """Whether two spans are one span, or one contains the other."""
    a0, a1 = a["span"]
    b0, b1 = b["span"]
    return (b0 <= a0 and b1 >= a1) or (a0 <= b0 and a1 >= b1)


def references_in(text: str) -> list[dict]:
    """Every reference in one message body, by the declared forms.

    Two forms can match one span — `html-file-key` wraps the token `file-token`
    also matches — and that is ONE finding. Two matches of one form are always
    two findings; two forms agree only when their spans touch AND they name the
    same id space, and then `FORM_PRECEDENCE` decides which one owns it and the
    other is recorded on the winner's edge as `also_matched_by`. Nothing is
    dropped silently: a form that stopped agreeing is a change in the evidence.
    Two attachments in one `post` are two references, which is the whole point —
    reading one attachment as two would put two edges on one material from one
    message and make a single attachment look like a re-post.
    """
    found: list[dict] = []
    for form, spec in REFERENCE_FORMS.items():
        for index, match in enumerate(spec["pattern"].finditer(text)):
            found.append({
                "form": form,
                "kind": spec["kind"](match),
                "id_space": spec["id_space"],
                "value": match.group(1),
                "part": f"{form}#{index}",
                "span": (match.start(1), match.end(1)),
                "identity_established": identity_established(match.group(1)),
            })
    ordered = sorted(found, key=lambda edge: (edge["span"][0],
                                              FORM_PRECEDENCE.index(edge["form"])))
    kept: list[dict] = []
    for candidate in ordered:
        candidate = {**candidate, "also_matched_by": []}
        rival = next((edge for edge in kept
                      if edge["id_space"] == candidate["id_space"]
                      and _nests(edge, candidate)), None)
        if rival is None:
            kept.append(candidate)
            continue
        candidate_wins = (FORM_PRECEDENCE.index(candidate["form"])
                          < FORM_PRECEDENCE.index(rival["form"]))
        winner, loser = (candidate, rival) if candidate_wins else (rival, candidate)
        if candidate_wins:
            kept[kept.index(rival)] = candidate
        winner["also_matched_by"].append(loser["form"])
    for edge in kept:
        edge.pop("span", None)
    return sorted(kept, key=lambda edge: (edge["part"], edge["value"]))


def _row_evidence(row: dict) -> dict:
    """One message row, projected to what this module is allowed to keep.

    `content` is scanned and dropped. Nothing returned here can carry the text
    of a message, which is what makes it safe for this module's output to be an
    artifact rather than a protected surface.
    """
    raw = row.get("content")
    text = raw if isinstance(raw, str) else ""
    sender = row.get("sender") if isinstance(row.get("sender"), dict) else {}
    return {
        "message_id": row.get("message_id"),
        "message_position": row.get("message_position"),
        "msg_type": row.get("msg_type"),
        "create_time": row.get("create_time"),
        "sender_type": sender.get("sender_type"),
        "updated": bool(row.get("updated") or row.get("edited")),
        "deleted": bool(row.get("deleted")),
        "references": references_in(text),
        "placeholder": _placeholder_of(text),
        "forwarded_lines": _forwarded_lines(text),
    }


def _placeholder_of(text: str):
    """The collector's own bracketed label, when that is all the body is.

    The vocabulary on this tree is the collector's: `[nonsupport]`,
    `[Sticker]`, `[Video call]`, and Chinese labels on the image and file rows
    it could not name. The label is returned VERBATIM and is never parsed: it
    is the writer's word for what it could not carry, and reading a count out
    of it would be exactly the prose-into-a-field move this module refuses.
    """
    match = re.match(r"^\s*\[([^\]\[]{0,60})\]\s*$", text)
    if not match:
        return None
    label = match.group(1).strip()
    if not label or re.search(r"\b(?:img|file)_v\d+_", label):
        return None
    return label


def _forwarded_lines(text: str) -> int:
    """How many sub-messages a flattened forward body shows, as a COUNT.

    §3 requires the forwarder, the parent and the visible originals to be kept
    and the traversal to be bounded. This record flattens a package into one
    body, so the depth is one by construction: the count is reported, the lines
    are not retained, and nothing here follows a reference into the group the
    package came from.
    """
    return len(re.findall(r"^\[\d{4}-\d{2}-\d{2}[^\]]*\]\s*[^:]{1,40}:", text,
                          flags=re.MULTILINE))


def read_messages(path: Path) -> dict:
    """Every row of one messages file, as evidence. Never a body.

    A missing file is a fact about the capture, not an error: a chat with no
    messages file has no message denominator at all, and §5 says that is not
    100% of anything.
    """
    if not path.is_file():
        return {"state": "NO_MESSAGES_FILE", "rows": [],
                "why": f"{path.name} is not beside this manifest: what this "
                       f"capture holds in messages was never written, so "
                       f"nothing here can be a message denominator"}
    rows: list[dict] = []
    try:
        handle = path.open(encoding="utf-8")
    except OSError as exc:
        return {"state": "UNREADABLE", "rows": [], "why": str(exc)}
    with handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError as exc:
                return {"state": "MALFORMED", "rows": rows,
                        "why": f"line {number}: {exc}"}
            if not isinstance(record, dict):
                return {"state": "MALFORMED", "rows": rows,
                        "why": f"line {number} is not an object"}
            rows.append(_row_evidence(record))
    return {"state": "READ", "rows": rows, "why": ""}


def row_state(row: dict) -> str:
    """What one row amounts to, by the declaration and not by the patterns.

    The label-only check sits ABOVE the placeholder one on purpose. A `sticker`
    row's whole content IS the collector's label, so asking about the
    placeholder first makes every sticker in the tree a gap and holds discovery
    off COMPLETE for a reason that is not a gap — the same defect §5 names, one
    level down. A label-only row that does carry a token is still REFERENCES:
    the declaration says what is expected of a type, not what it may never hold.
    """
    if row["msg_type"] in OPAQUE_TYPES:
        return "UNSUPPORTED_TYPE"
    if row["references"]:
        return "REFERENCES"
    if row["msg_type"] in LABEL_ONLY_TYPES:
        return "NO_REFERENCE_EXPECTED"
    if row["placeholder"] is not None:
        return "PLACEHOLDER"
    if row["msg_type"] in MATERIAL_TYPES:
        return "EMPTY_IN_A_MATERIAL_TYPE"
    return "NO_REFERENCE_EXPECTED"


# -------------------------------------------------------------- the on-disk --
def landed_files(directory: Path, *, digest: bool = False) -> dict:
    """What is actually in a capture's `files/` directory, by name.

    Sizes are read for every file because one `stat` is cheap and a declared
    size that disagrees with the bytes is the §5 finding "只有登记hash、实际字节
    丢失也不能称归档完整" caught at the moment it appears. Digests are read only
    when the caller asks: they cost a full read of every image, and a caller who
    wants the landing state should not pay for a hash it will not compare.
    """
    out: dict = {}
    root = files_dir(directory)
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        row = {"bytes": size, "path": str(path.relative_to(root.parent.parent))}
        if digest:
            row["sha256"] = _sha256(path)
        out[path.name] = row
    return out


def _sha256(path: Path) -> str | None:
    """The digest of one file, or None when it cannot be read."""
    try:
        h = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


# ------------------------------------------------------------- the manifests --
def read_manifest(path: Path) -> dict:
    """One manifest as a mapping. A file that cannot be read is a `Malformed`."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Malformed(f"cannot read {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise Malformed(f"{path} holds {type(document).__name__}, not an object")
    return document


def manifest_evidence(document: dict) -> dict:
    """What a manifest claims about its own materials, kept apart by claim.

    The legacy map, the backfilled list and the pending sentence are three
    different kinds of claim and they are returned as three fields. §4 is
    explicit that the map is navigation only; §5 is explicit that the sentence
    is not a denominator. Merging them here would be the collapse this module
    exists to prevent, one level down.
    """
    files = document.get("files") if isinstance(document.get("files"), dict) else {}
    links = document.get("links") if isinstance(document.get("links"), dict) else {}
    legacy: dict = {}
    for message_id, entry in (files.get("by_message") or {}).items():
        if isinstance(entry, dict):
            legacy[str(message_id)] = {
                "file_key": entry.get("file_key"),
                "path": entry.get("path"),
                "bytes": entry.get("bytes"),
                "msg_type": entry.get("msg_type"),
                "form": entry.get("form"),
                "media_kind": entry.get("media_kind"),
                "downloaded_at": entry.get("downloaded_at"),
            }
    return {
        "legacy_by_message": legacy,
        "backfilled": [str(name) for name in (files.get("backfilled") or [])
                       if isinstance(name, (str, int))],
        "image_keys": [str(name) for name in (files.get("image_keys") or [])
                       if isinstance(name, (str, int))],
        "pending_text": files.get("pending") if isinstance(files.get("pending"), str)
                        else None,
        "links_expanded": [str(name) for name in (links.get("expanded") or [])],
        "links_pending": links.get("pending") if isinstance(links.get("pending"), str)
                         else None,
        "media": [str(name) for name in (document.get("media") or [])],
        "redaction": document.get("redaction_applied")
                     or (document.get("redactions") or None),
    }


def link_objects(directory: Path) -> list[dict]:
    """Every link object the collector expanded below a chat, read as evidence.

    Each one is a material in its own right — its `url` is the identity and its
    `media` entries are materials OF it — and each carries the message it came
    from. That `source_message_id` is the discovery edge that ties the
    collector's expansion back to the message side, which is why a count of
    these is never a count of what the messages reference.
    """
    out: list[dict] = []
    root = Path(directory) / "links"
    if not root.is_dir():
        return out
    for path in sorted(root.rglob(MANIFEST_NAME)):
        try:
            document = read_manifest(path)
        except Malformed as exc:
            out.append({"dir": str(path.parent), "state": "UNREADABLE",
                        "why": str(exc)})
            continue
        out.append({
            "dir": str(path.parent),
            "state": "READ",
            "url": document.get("url"),
            "type": document.get("type"),
            "title": document.get("title"),
            "source_message_id": document.get("source_message_id"),
            "content_sha256": document.get("content_sha256"),
            "fetched_at": document.get("fetched_at"),
            "media": [str(name) for name in (document.get("media") or [])],
        })
    return out


# ------------------------------------------------------------ material keys --
def material_id(provider: str, key_space: str, value: str, scope: str) -> str:
    """The stable key for one material.

    §4 gives the identity as provider/tenant/object plus revision and part, and
    warns that a token is neither globally unique nor permanent. So a token
    material is keyed within the scope that holds it — the same token in two
    captures is two materials with two edge sets, and whether their bytes are
    the same file is not decided here. A document material is keyed by its URL
    in the same space `source_policy` keys documents in, because the same
    document referenced from a message and expanded by the collector is ONE
    material with two discovery edges and must not be two.

    A value carrying the pipeline's redaction marker is the exception in the
    other direction: it is not an identity, so the scope it was found in is part
    of its key. Without that, two different share links redacted the same way
    become one material with two sources, which is the §4 failure exactly —
    several materials reported as one.
    """
    if not identity_established(value):
        return f"{provider}:{key_space}:{scope}:{value}"
    if key_space == "doc":
        return f"{provider}:doc:{value}"
    return f"{provider}:material:{scope}:{value}"


def _scope_of(record: dict, document: dict) -> tuple:
    """The scope a capture object's token materials are keyed within."""
    chat = document.get("chat") if isinstance(document.get("chat"), dict) else {}
    chat_id = chat.get("chat_id")
    if isinstance(chat_id, str) and chat_id:
        return chat_id, "chat_id"
    directory = Path(str(record.get("path", ""))).parent.name
    return directory, "directory_name"


def source_key(record: dict, document: dict) -> str:
    """`source_policy.key_of`'s identity for this object, in the same shape.

    Composed here rather than imported so a reader's key space does not depend
    on another module's internals; `tests/test_material_index.py` asserts the
    two agree for every object on the live tree, which is what keeps them from
    drifting.
    """
    provider = provider_of(record.get("path", ""))
    chat = document.get("chat") if isinstance(document.get("chat"), dict) else {}
    chat_id = chat.get("chat_id")
    if isinstance(chat_id, str) and chat_id:
        return f"{provider}:chat:{chat_id}"
    url = document.get("url")
    if isinstance(url, str) and url:
        return f"{provider}:doc:{url}"
    return f"{provider}:chat:{Path(str(record.get('path', ''))).parent.name}"


#: Edge producers. Two of them and only two: a message that names a material,
#: and a link object the collector expanded. A manifest's own lists are NOT a
#: third — a list says a material was handled and names no source for it, so it
#: is a registry claim and never an edge. That separation is what makes
#: "the legacy map is navigation only" a property of the shape and not a note.
EDGE_PRODUCERS = ("MESSAGE_CONTENT", "LINK_OBJECT")

#: Facts, and which half each belongs to. One table for all halves because the
#: guard's job is to refuse an unnamed field, and a field named in the wrong
#: half is caught by the half's own assertions in the suite.
FACTS_PRODUCED = {
    "rows": "the message rows read from this object's messages file",
    "by_state": "those rows counted by `row_state`",
    "by_type": "those rows counted by `msg_type`",
    "with_references": "the rows that matched at least one declared form",
    "gap_rows": "the rows that keep discovery off COMPLETE",
    "materials": "the materials this object's evidence found, by id",
    "landed": "materials whose bytes are on disk",
    "absent": "materials with no bytes on disk",
    "registered_not_landed": "manifests claiming bytes the disk does not hold",
    "referenced_not_landed": "messages naming a material nothing landed",
    "locator_only": "unlanded materials the policy allows to stay locators",
    "stale_markers": "registry entries the measured bytes contradict",
    "unindexed": "bytes on disk no manifest list names",
    "declared_bytes_disagree": "materials whose declared and measured sizes "
                               "are both present and differ",
    "redacted_identity": "materials whose only name was replaced by the capture "
                         "pipeline's redaction marker, so no fetch, read-back or "
                         "consumer can resolve them",
    "sources": "the capture objects whose evidence reached this half",
}

DISCOVERY_HALF_PRODUCED = {
    "state": "one of DISCOVERY_STATES, worst first",
    "measured_by": "`intake/material_index.py` — a count over the message rows",
    "facts": "the counts this half was decided from",
    "gaps": "每一行都不放过的原因: the rows that are not `REFERENCES` and not "
            "`NO_REFERENCE_EXPECTED`, each with its own evidence",
    "refusal": "present when the half could not be decided at all",
    "why": "one sentence per reason the state is not COMPLETE",
}

MATERIAL_HALF_PRODUCED = {
    "state": "one of MATERIAL_STATES, worst first",
    "measured_by": "`intake/material_index.py`, against the policy row the "
                   "caller passed — never against an assumed depth",
    "required_by": "the policy labels copied from `source_policy`, or null",
    "facts": "the counts this half was decided from",
    "gaps": "the materials that keep this half off COMPLETE, each with the "
            "state that made it a gap",
    "refusal": "present when no policy row was passed",
    "why": "one sentence per reason the state is not COMPLETE",
}

MESSAGE_HALF_PRODUCED = {
    "state": "the state `capture_coverage` decided, copied",
    "measured_by": "`intake/capture_coverage.py` — this module does not "
                   "re-derive it, because a second answer to one question is "
                   "the failure the two modules were split to prevent",
    "copied": "that module's whole component, copied rather than summarised",
    "why": "one sentence per reason it is here: that it was copied, and from "
           "which object",
}

UNDERSTANDING_HALF_PRODUCED = {
    "state": "`NOT_MEASURED` when this module did not look, otherwise "
             "`source_waterline`'s comparison state for this object copied "
             "verbatim — its vocabulary is that module's `COMPARISON_STATES` and "
             "is deliberately not enumerated here",
    "measured_by": "whoever measured it, named. `NOT_MEASURED` names "
                   "`intake/source_waterline.py` as the producer that answers "
                   "this and says this module did not look",
    "copied": "`source_waterline`'s whole row for this object, copied rather "
              "than summarised, when the caller supplied one",
    "why": "one sentence per reason",
}


def _half(state: str, measured_by: str, facts=None, gaps=None, refusal=None,
          copied=None, why=None, required_by=None,
          table: dict = DISCOVERY_HALF_PRODUCED) -> dict:
    """One coverage half. A key the table does not name refuses at emission."""
    out = {"state": state, "measured_by": measured_by}
    if copied is not None:
        out["copied"] = copied
    if required_by is not None:
        out["required_by"] = required_by
    if facts is not None:
        no_unsourced(facts, FACTS_PRODUCED, "facts")
        out["facts"] = facts
    if gaps:
        out["gaps"] = gaps
    if refusal:
        out["refusal"] = refusal
    out["why"] = list(why or [])
    return no_unsourced(out, table, "coverage")


# ------------------------------------------------------------------ discovery --
def discovery_coverage(messages: dict, *, shape: str | None = None,
                       limit: int | None = None) -> dict:
    """Whether the messages this capture holds were all examined.

    §5 asks this as its own question with its own denominator, and the
    denominator is the rows the capture wrote — not the collector's count of
    its own work, and not the number of materials found. A row that is
    `PLACEHOLDER`, `EMPTY_IN_A_MATERIAL_TYPE` or `UNSUPPORTED_TYPE` keeps this
    at `PARTIAL`; no combination of zeroes makes it `COMPLETE`.

    A document capture has no message stream by construction, so the missing
    file is `NOT_APPLICABLE` and not `UNREADABLE`: reporting 20 document
    captures as unread would say this module failed at 20 objects when it
    failed at none. `NOT_APPLICABLE` is its own state and is not read as a
    pass — coverage for a document lives in its own `body` component, which is
    `capture_coverage`'s to report.
    """
    if messages["state"] == "NO_MESSAGES_FILE" and shape == "document_capture":
        return _half("NOT_APPLICABLE", "intake/material_index.py",
                     facts={"rows": 0, "by_state": {}, "by_type": {},
                            "with_references": 0, "gap_rows": 0},
                     why=["this object is a document captured from a chat: it "
                          "has no message stream, so the message-discovery "
                          "question has no denominator here and this is not a "
                          "pass. What this document holds is its own `body` "
                          "component, in `capture_coverage`"])
    if messages["state"] != "READ":
        return _half("UNREADABLE", "intake/material_index.py",
                     refusal={"half": "discovery",
                              "what_would_decide_it":
                                  "a readable messages file beside the manifest",
                              "why": messages["why"]},
                     why=[messages["why"]])
    rows = messages["rows"]
    if not rows:
        return _half("NONE_MEASURED", "intake/material_index.py",
                     facts={"rows": 0, "by_state": {}, "by_type": {},
                            "with_references": 0, "gap_rows": 0},
                     refusal={"half": "discovery",
                              "what_would_decide_it":
                                  "at least one message row to be the "
                                  "denominator. 0 rows and 0 materials is not "
                                  "100% of anything — §5 forbids reading an "
                                  "absent denominator as a complete one",
                              "why": "this capture wrote no message rows"},
                     why=["this capture wrote no message rows"])
    by_state: dict = {}
    by_type: dict = {}
    gaps: list[dict] = []
    with_references = 0
    for row in rows:
        state = row_state(row)
        by_state[state] = by_state.get(state, 0) + 1
        by_type[row["msg_type"]] = by_type.get(row["msg_type"], 0) + 1
        if state == "REFERENCES":
            with_references += 1
            continue
        if state == "NO_REFERENCE_EXPECTED":
            continue
        gaps.append({
            "message_id": row["message_id"],
            "message_position": row["message_position"],
            "msg_type": row["msg_type"],
            "state": state,
            "placeholder": row["placeholder"],
            "forwarded_lines": row["forwarded_lines"],
            "why": _gap_why(state, row),
        })
    facts = {"rows": len(rows), "by_state": by_state, "by_type": by_type,
             "with_references": with_references, "gap_rows": len(gaps)}
    if gaps:
        shown = gaps if limit is None else gaps[:limit]
        return _half("PARTIAL", "intake/material_index.py", facts=facts,
                     gaps=shown,
                     why=_discovery_why(by_state, by_type, gaps))
    return _half("COMPLETE", "intake/material_index.py", facts=facts)


def _gap_why(state: str, row: dict) -> str:
    """Why one row is a gap, in the row's own terms."""
    if state == "UNSUPPORTED_TYPE":
        return (f"the collector recorded `{row['msg_type']}`: it could not "
                f"render this message, so whether it carried material is not "
                f"answered by anything on this node. §3: 没有解析到不能等于没有材料")
    if state == "PLACEHOLDER":
        return (f"the body is the collector's own label `{row['placeholder']}` "
                f"and no declared form matched it: the material this message "
                f"stood for was not written into the record")
    return (f"the row is a `{row['msg_type']}`, a type that carries material, "
            f"and no declared form found a reference in it — so either it had "
            f"none or the forms miss this one, and the record cannot tell the "
            f"two apart")


def _discovery_why(by_state: dict, by_type: dict, gaps: list[dict]) -> list[str]:
    """The sentences a reader needs, worst evidence first."""
    why: list[str] = []
    if by_state.get("UNSUPPORTED_TYPE"):
        why.append(
            f"{by_state['UNSUPPORTED_TYPE']} message(s) are of a type the "
            f"collector itself recorded as unsupported. Every one of them is a "
            f"message whose material is unknown, not absent")
    if by_state.get("PLACEHOLDER"):
        labels = sorted({row["placeholder"] for row in gaps
                         if row["state"] == "PLACEHOLDER"})
        why.append(
            f"{by_state['PLACEHOLDER']} message(s) carry the collector's own "
            f"label instead of the material ({', '.join(labels[:5])}"
            f"{'…' if len(labels) > 5 else ''})")
    if by_state.get("EMPTY_IN_A_MATERIAL_TYPE"):
        types = sorted({row["msg_type"] for row in gaps
                        if row["state"] == "EMPTY_IN_A_MATERIAL_TYPE"})
        why.append(
            f"{by_state['EMPTY_IN_A_MATERIAL_TYPE']} message(s) are of "
            f"material-bearing types ({', '.join(types)}) and yielded no "
            f"reference: this is the count §4 forbids turning into "
            f"`0 materials, therefore complete`")
    why.append(
        f"{len(gaps)} of {sum(by_state.values())} rows are gaps, so discovery "
        f"coverage for this capture is PARTIAL whatever the material count is")
    return why


# ------------------------------------------------------------------- material --
def _landing(material: dict, disk: dict, *, digest: bool) -> dict:
    """Where the bytes are, measured, with the hash's own state named."""
    names = material["_candidate_names"]
    found = next((name for name in names if name in disk), None)
    if found is None:
        return no_unsourced({
            "state": "ABSENT", "bytes": None,
            "hash_state": "NOT_MEASURED",
            "why": [f"no file under `files/` for any of "
                    f"{', '.join(sorted(names)) or 'no name at all'}"],
        }, LANDING_PRODUCED, "landing")
    entry = disk[found]
    measured = _number(entry.get("bytes"))
    hash_state = ("MEASURED" if entry.get("sha256")
                  else "NOT_MEASURED" if not digest
                  else "UNREADABLE")
    return no_unsourced({
        "state": "LANDED", "bytes": measured,
        "sha256": entry.get("sha256"),
        "hash_state": hash_state,
        "why": [] if digest else
               ["the bytes were not hashed: one `stat` per file is cheap and a "
                "digest over every image is not, so a caller that will not "
                "compare a digest does not pay for one"],
    }, LANDING_PRODUCED, "landing")


def _registry(material: dict, evidence: dict) -> dict:
    """What the manifests claim about this material, kept apart by claim."""
    states: list[str] = []
    names: list[str] = []
    declared_bytes = None
    pending_text = None
    why: list[str] = []
    value = material["token"] or material["url"] or ""
    for name in evidence["backfilled"]:
        if _token_of(name) == value:
            states.append("DECLARED_BACKFILLED")
            names.append(name)
    for name in evidence["image_keys"]:
        if _token_of(name) == value:
            states.append("DECLARED_PENDING")
            names.append(name)
    for message_id, entry in evidence["legacy_by_message"].items():
        if entry.get("file_key") == value:
            states.append("IN_LEGACY_BY_MESSAGE")
            names.append(str(entry.get("path") or entry.get("file_key")))
            if declared_bytes is None:
                declared_bytes = _number(entry.get("bytes"))
    for name in evidence["media"]:
        if name == value or _token_of(name) == value:
            states.append("IN_LINK_MEDIA")
            names.append(name)
    if "DECLARED_PENDING" in states and material["landing"]["state"] == "LANDED":
        why.append(
            "a manifest says this material is pending AND the bytes are on "
            "disk: the marker is stale, and §10's row for it says to "
            "reconcile the index, not to download it again")
        pending_text = evidence["pending_text"]
    if not states:
        why.append("no manifest list names this material")
    return no_unsourced({
        "states": sorted(set(states)),
        "name_on_disk": material["landing"].get("locator"),
        "names_registered": sorted(set(names)),
        "declared_bytes": declared_bytes,
        "pending_text": pending_text,
        "why": why,
    }, REGISTRY_PRODUCED, "registry")


def _token_of(name: str) -> str:
    """The token a manifest's file name carries.

    A token is registered WITHOUT its suffix and lands WITH it — the legacy map
    stores `file_key: img_v3_…` beside `path: files/img_v3_….jpg` — so the join
    is on the name up to its first suffix and never on the raw name. Joining on
    the raw name is how the 58-entry map looks like a 0-entry one.
    """
    text = str(name)
    head = text.rsplit("/", 1)[-1]
    return head.split(".", 1)[0] if "." in head else head


def _material_record(seed: dict, edges: list[dict], evidence: dict, disk: dict,
                     *, provider: str, digest: bool) -> dict:
    """One material: its edges, its landing, its registry, all three apart.

    A material may have NO edges and still be a material: a `files.backfilled`
    name whose message our forms did not find one in is a real finding — the
    collector handled something our discovery did not see — and dropping it for
    want of an edge would delete exactly the evidence that our forms are
    incomplete.
    """
    kinds = sorted({edge["kind"] for edge in edges})
    token = seed["value"] if seed["id_space"] == "material" else None
    url = seed["value"] if seed["id_space"] == "doc" else None
    messages = sorted({edge["message_id"] for edge in edges
                       if edge.get("message_id")})
    senders = sorted({str(edge["sender_type"]) for edge in edges
                      if edge.get("sender_type")})
    times = sorted(str(edge["create_time"]) for edge in edges
                   if edge.get("create_time"))
    material = {
        "material_id": seed["material_id"], "provider": provider,
        "key_space": seed["id_space"],
        "kind": kinds[0] if len(kinds) == 1 else ("mixed" if kinds else "unknown"),
        "token": token, "url": url,
        "identity_state": ("ESTABLISHED" if seed["identity_established"]
                           else "REDACTED"),
        "scope": seed["scope"], "scope_source": seed["scope_source"],
        "sources": sorted({edge["source"] for edge in edges}),
        "edges": len(edges), "messages": len(messages),
        "senders": senders,
        "message_time": {"earliest": times[0], "latest": times[-1]} if times
                        else {"earliest": None, "latest": None},
        "discovery_forms": sorted({edge["form"] for edge in edges}),
        "snapshots": [edge["snapshot"] for edge in edges
                      if edge.get("snapshot")],
        "_candidate_names": sorted(
            set(_names_for(evidence, token, url))
            | {name for name in disk if token and _token_of(name) == token}),
    }
    material["landing"] = _landing(material, disk, digest=digest)
    material["registry"] = _registry(material, evidence)
    material["declared_bytes"] = material["registry"]["declared_bytes"]
    measured = material["landing"]["bytes"]
    material["bytes_agree"] = (None if measured is None
                               or material["declared_bytes"] is None
                               else measured == material["declared_bytes"])
    material["mime"], material["mime_source"] = _mime_of(material, evidence)
    material["name"] = _first_name(evidence, token, url)
    material["locator"] = material["landing"].get("locator")
    material["time"] = _downloaded_at(evidence, token, url)
    material["permission"], material["retention"] = _absent_fields(evidence)
    material["redaction"] = evidence["redaction"]
    material["why"] = _material_why(material, evidence)
    material.pop("_candidate_names")
    return no_unsourced(material, MATERIAL_PRODUCED, "material")


def _names_for(evidence: dict, token, url) -> list[str]:
    """Every file name any evidence uses for this material, before the join."""
    names: list[str] = []
    for name in evidence["backfilled"] + evidence["image_keys"]:
        if _token_of(name) == token:
            names.append(name)
    for entry in evidence["legacy_by_message"].values():
        if entry.get("file_key") == token and entry.get("path"):
            names.append(str(entry["path"]).rsplit("/", 1)[-1])
    if url:
        names.append(url)
    return names


def _mime_of(material: dict, evidence: dict) -> tuple:
    """The MIME type, and which of the two sources it came from."""
    for entry in evidence["legacy_by_message"].values():
        if entry.get("file_key") == material["token"] and entry.get("media_kind"):
            return entry["media_kind"], "media_kind"
    for name in evidence["backfilled"]:
        if _token_of(name) == material["token"] and "." in name:
            return name.rsplit(".", 1)[-1].lower(), "name_suffix"
    return None, None


def _first_name(evidence: dict, token, url) -> str | None:
    for name in evidence["backfilled"]:
        if _token_of(name) == token:
            return name
    for entry in evidence["legacy_by_message"].values():
        if entry.get("file_key") == token:
            return str(entry.get("path") or "") or None
    return url


def _downloaded_at(evidence: dict, token, url):
    times = sorted(str(entry["downloaded_at"])
                   for entry in evidence["legacy_by_message"].values()
                   if entry.get("file_key") == token and entry.get("downloaded_at"))
    return times[0] if times else None


def _absent_fields(evidence: dict) -> tuple:
    """Permission and retention, or the statement that nobody recorded them.

    §5: 权限拒绝结束 attempt 而不是 content 成功 — so a permission refusal is
    reported as a fact about the attempt and never as coverage.
    """
    recorded = {"state": "NOT_RECORDED",
                "why": "neither the manifest nor the legacy map carries a "
                       "permission or retention field, so what may be done "
                       "with these bytes is not on record here"}
    return dict(recorded), {"state": "NOT_RECORDED", "why": recorded["why"]}


def _material_why(material: dict, evidence: dict) -> list[str]:
    why: list[str] = []
    if material["identity_state"] == "REDACTED":
        why.append(
            "every name this material was given carries the capture pipeline's "
            "redaction marker, so it is one OCCURRENCE and not an address: it "
            "is keyed within the message that named it, it can never be "
            "fetched, landed or read back, and this half reports it as an "
            "unclosable gap rather than as a locator")
    if material["landing"]["state"] == "ABSENT":
        if "DECLARED_BACKFILLED" in material["registry"]["states"]:
            why.append(
                "a manifest lists this among the backfilled materials and no "
                "bytes are on disk: §5, 只有登记hash、实际字节丢失也不能称归档完整")
        elif material["edges"] and material["identity_state"] == "ESTABLISHED":
            why.append(
                "messages reference this material and nothing has landed for "
                "it: it is a locator, and whether that is enough is the source "
                "policy's question and not this module's")
    if material["bytes_agree"] is False:
        why.append(
            f"the manifest declares {material['declared_bytes']} bytes and the "
            f"file on disk is {material['landing']['bytes']}: the record and "
            f"the bytes disagree")
    return why


# ------------------------------------------------------- the material half ----
def material_coverage(materials: list[dict], policy_row, *,
                      object_key: str, discovery_state: str | None = None) -> dict:
    """Which required bytes landed, decided against the policy and not a depth.

    `policy_row` is `source_policy.resolve`'s own row for this object, copied.
    With no row the half is `UNMEASURABLE`: an unlanded material is a gap under
    `DEEP_REQUIRED` and an allowed locator under `SHALLOW`, so a reader that
    assumed either one would be deciding the depth the policy exists to decide.

    `discovery_state` is what makes a zero a measurement. §5's rule is that 0
    materials over an unknown parser must not read as 100%; it is not a rule
    that a source holding nothing can never be closed. When the discovery half
    is `COMPLETE` the rows were all examined and a zero is measured, so the
    half is `COMPLETE` and says so. Otherwise it is `UNMEASURABLE`, because
    there is no denominator to divide by rather than a zero to report.
    """
    if policy_row is None:
        return _half("UNMEASURABLE", "intake/material_index.py",
                     facts={"materials": len(materials),
                            "landed": _count(materials, "LANDED"),
                            "absent": _count(materials, "ABSENT"),
                            "registered_not_landed": _count_registered(materials),
                            "referenced_not_landed": _count_referenced(materials),
                            "locator_only": 0, "stale_markers":
                                len(_stale_markers(materials)),
                            "unindexed": len(_unindexed(materials)),
                            "redacted_identity": len(
                                [m for m in materials
                                 if m["identity_state"] == "REDACTED"]),
                            "declared_bytes_disagree": _count_disagree(materials)},
                     table=MATERIAL_HALF_PRODUCED,
                     refusal={"half": "material",
                              "what_would_decide_it":
                                  "a `source_policy` row for this source. The "
                                  "policy is what makes an unlanded material a "
                                  "gap or a locator, and this module will not "
                                  "assume DEEP_REQUIRED",
                              "why": f"no policy row was passed for {object_key}"},
                     why=["no policy row was passed, so required-landing "
                          "cannot be decided and no state is claimed"])
    labels = list(policy_row.get("policies") or [])
    deep = "DEEP_REQUIRED" in labels
    if "NONE" in labels:
        return _half("NONE_REQUIRED_BY_POLICY", "intake/material_index.py",
                     facts={"materials": len(materials),
                            "landed": _count(materials, "LANDED"),
                            "absent": _count(materials, "ABSENT"),
                            "registered_not_landed": 0,
                            "referenced_not_landed": 0, "locator_only": 0,
                            "stale_markers": len(_stale_markers(materials)),
                            "unindexed": len(_unindexed(materials)),
                            "redacted_identity": len(
                                [m for m in materials
                                 if m["identity_state"] == "REDACTED"]),
                            "declared_bytes_disagree": _count_disagree(materials)},
                     table=MATERIAL_HALF_PRODUCED,
                     required_by=labels,
                     why=["the policy in force for this source is `NONE`: no "
                          "material closure was ever an obligation here, and "
                          "that a capture exists at all is the finding — "
                          "`source_policy` reports it as "
                          "`DENIED_BUT_CAPTURED`"])
    gaps: list[dict] = []
    locator_only = 0
    for material in materials:
        landing = material["landing"]["state"]
        if landing == "LANDED":
            continue
        registered = "DECLARED_BACKFILLED" in material["registry"]["states"]
        if material["identity_state"] == "REDACTED":
            # Not about depth: no policy makes a redacted address resolvable.
            # §5 asks whether every REQUIRED material landed, and this one can
            # never land, so calling it a locator would be the same mistake as
            # calling a permission refusal a success.
            gaps.append({"material_id": material["material_id"],
                         "state": "IDENTITY_REDACTED",
                         "why": "the only name this material has was replaced "
                                "by the pipeline's redaction marker, so it is "
                                "not a locator anything can follow and not a "
                                "material that can be closed"})
        elif registered:
            gaps.append({"material_id": material["material_id"],
                         "state": "REGISTERED_NOT_LANDED",
                         "why": "a manifest lists it among the backfilled "
                                "materials and the bytes are not on disk"})
        elif deep:
            gaps.append({"material_id": material["material_id"],
                         "state": "REFERENCED_NOT_LANDED",
                         "why": "the policy in force is DEEP_REQUIRED, which "
                                "makes the material closure an obligation to "
                                "complete, so a locator is not enough"})
        else:
            locator_only += 1
    facts = {"materials": len(materials),
             "landed": _count(materials, "LANDED"),
             "absent": _count(materials, "ABSENT"),
             "registered_not_landed":
                 len([gap for gap in gaps
                      if gap["state"] == "REGISTERED_NOT_LANDED"]),
             "referenced_not_landed":
                 len([gap for gap in gaps
                      if gap["state"] == "REFERENCED_NOT_LANDED"]),
             "redacted_identity":
                 len([gap for gap in gaps
                      if gap["state"] == "IDENTITY_REDACTED"]),
             "locator_only": locator_only,
             "stale_markers": len(_stale_markers(materials)),
             "unindexed": len(_unindexed(materials)),
             "declared_bytes_disagree": _count_disagree(materials)}
    if not materials:
        if discovery_state == "COMPLETE":
            return _half("COMPLETE", "intake/material_index.py", facts=facts,
                         table=MATERIAL_HALF_PRODUCED,
                         required_by=labels,
                         why=["no material was found and the discovery half is "
                              "COMPLETE over this object's rows: every row was "
                              "examined, so this is a measured zero and not an "
                              "absent denominator"])
        return _half("UNMEASURABLE", "intake/material_index.py", facts=facts,
                     table=MATERIAL_HALF_PRODUCED,
                     required_by=labels,
                     refusal={"half": "material",
                              "what_would_decide_it":
                                  "至少一个材料, or a discovery half that is "
                                  "COMPLETE and says this source holds none. 0 "
                                  "materials over 0 landed bytes is not 100% — "
                                  "§5's `0材料且parser未知不得0/0=100%`, and the "
                                  "parser's state here is "
                                  f"{discovery_state or 'not passed'}, not "
                                  f"COMPLETE",
                              "why": "no material was found for this object "
                                     "and its discovery half does not "
                                     "establish that it holds none"},
                     why=["no materials were found, and with no denominator "
                          "this half claims nothing"])
    why: list[str] = []
    if gaps:
        why.append(f"{len(gaps)} material(s) the policy requires are not "
                   f"landed")
    if facts["redacted_identity"]:
        why.append(
            f"{facts['redacted_identity']} of those material(s) can never land: "
            f"the capture pipeline replaced the only name they had. That is a "
            f"finding about the record's desensitisation and not a fetch that "
            f"has not happened yet, and no policy depth closes it")
    if locator_only:
        why.append(f"{locator_only} material(s) are unlanded and the policy in "
                   f"force ({', '.join(labels)}) does not require their bytes; "
                   f"they are reported as locators and not as gaps")
    if facts["stale_markers"]:
        why.append(f"{facts['stale_markers']} material(s) are declared pending "
                   f"AND landed: the marker is stale (§10: reconcile, do not "
                   f"download again)")
    if facts["unindexed"]:
        why.append(f"{facts['unindexed']} file(s) are on disk and no manifest "
                   f"list names them")
    state = "PARTIAL" if gaps else "COMPLETE"
    return _half(state, "intake/material_index.py", facts=facts, gaps=gaps,
                 table=MATERIAL_HALF_PRODUCED,
                 required_by=labels,
                 why=why)


# ------------------------------------------------------------- one object ----
def build_object(record: dict, document: dict, directory: Path, *,
                 policy_row, understanding, digest: bool) -> tuple:
    """One capture object: its materials, its four halves, its collisions.

    Returns `(object_row, materials, collisions)`. The materials are returned
    rather than stored so the document can merge them across objects: §4's
    "one material, several message sources" is only expressible if the object
    that found a material and the object that owns its name are two facts.
    """
    provider = provider_of(record.get("path", ""))
    scope, scope_source = _scope_of(record, document)
    key = source_key(record, document)
    messages = read_messages(messages_path(directory))
    evidence = manifest_evidence(document)
    disk = landed_files(directory, digest=digest)
    links = link_objects(directory)

    seeds: dict = {}
    edges: dict = {}

    def seed_for(id_space: str, value: str, *, where_scope: str,
                 where_source: str) -> dict:
        mid = material_id(provider, id_space, value, where_scope)
        if mid not in seeds:
            seeds[mid] = {"material_id": mid, "id_space": id_space,
                          "value": value, "scope": where_scope,
                          "scope_source": where_source,
                          "identity_established": identity_established(value)}
        return seeds[mid]

    def add_edge(mid: str, edge: dict) -> None:
        edges.setdefault(mid, []).append(edge)

    # -- the messages. The only producer that ties a material to a message.
    for row in messages["rows"]:
        for reference in row["references"]:
            # A redacted value names no material, so it is keyed to the
            # occurrence that produced it and not to the value: §4's "one
            # material, several sources" is a claim about identity, and two
            # different links the pipeline replaced with one marker do not have
            # one. The discriminator is the message and the part, both of which
            # are in the record.
            named = reference["identity_established"]
            seed = seed_for(
                reference["id_space"], reference["value"],
                where_scope=(scope if named
                             else f"{scope}#{row['message_id']}"
                                  f"#{reference['part']}"),
                where_source=scope_source if named else "redacted_identity")
            add_edge(seed["material_id"], {
                "producer": "MESSAGE_CONTENT", "form": reference["form"],
                "kind": reference["kind"], "id_space": reference["id_space"],
                "value": reference["value"], "part": reference["part"],
                "also_matched_by": reference["also_matched_by"],
                "identity_established": reference["identity_established"],
                "message_id": row["message_id"],
                "message_position": row["message_position"],
                "msg_type": row["msg_type"], "create_time": row["create_time"],
                "sender_type": row["sender_type"],
                "parent_forward_id": (row["message_id"]
                                      if row["msg_type"] == "merge_forward"
                                      else None),
                "association": ("WEAK" if row["msg_type"] == "merge_forward"
                                else "STRONG"),
                "revision": {"updated": row["updated"], "deleted": row["deleted"]},
                "snapshot": None,
                "source": record.get("path"),
            })

    # -- the collector's expansions. A second producer, not a count of the
    #    first: what a link object holds is what the collector fetched, and
    #    what the messages hold is what was said, and the two numbers differ.
    for index, link in enumerate(links):
        if link["state"] != "READ":
            continue
        if link.get("url"):
            seed = seed_for("doc", link["url"], where_scope=scope,
                            where_source=scope_source)
            add_edge(seed["material_id"], {
                "producer": "LINK_OBJECT", "form": "link-object",
                "kind": "web", "id_space": "doc", "value": link["url"],
                "part": f"link-object#{index}", "also_matched_by": [],
                "identity_established": identity_established(link["url"]),
                "message_id": link.get("source_message_id"),
                "message_position": None, "msg_type": None,
                "create_time": None, "sender_type": None,
                "parent_forward_id": None, "association": "STRONG",
                "revision": None,
                "source": record.get("path"),
                "snapshot": {"dir": link["dir"], "title": link.get("title"),
                             "type": link.get("type"),
                             "content_sha256": link.get("content_sha256"),
                             "fetched_at": link.get("fetched_at")},
            })
        link_scope = f"{scope}#{Path(link['dir']).name}"
        for position, name in enumerate(link["media"]):
            seed = seed_for("material", name, where_scope=link_scope,
                            where_source="link_directory")
            add_edge(seed["material_id"], {
                "producer": "LINK_OBJECT", "form": "link-media",
                "kind": "attachment", "id_space": "material", "value": name,
                "part": f"link-media#{position}", "also_matched_by": [],
                "identity_established": identity_established(name),
                "message_id": link.get("source_message_id"),
                "message_position": None, "msg_type": None,
                "create_time": None, "sender_type": None,
                "parent_forward_id": None, "association": "STRONG",
                "revision": None,
                "source": record.get("path"),
            })

    # -- the registry-only seeds. A list says a material was handled and names
    #    no message, so it seeds a material and produces no edge: an object
    #    with `edges: 0` and a backfilled claim is the finding that our forms
    #    did not see what the collector handled.
    for name in evidence["backfilled"] + evidence["image_keys"]:
        token = _token_of(name)
        if _is_token(token):
            seed_for("material", token, where_scope=scope,
                     where_source=scope_source)
    for entry in evidence["legacy_by_message"].values():
        if _is_token(entry.get("file_key")):
            seed_for("material", entry["file_key"], where_scope=scope,
                     where_source=scope_source)

    # -- the bytes themselves. A file under `files/` is evidence that a
    #    material exists, whatever the messages and the lists say about it, and
    #    §4's material discovery asks what is there and not only what was
    #    named. Without this seeding a file no list mentions is invisible: the
    #    module reported 73 landed materials against 112 token-named files on
    #    disk and called the index complete, which is the understatement §10's
    #    `现存文件pending旧 → 核字节/关联后修索引` row is about.
    for name in disk:
        token = _token_of(name)
        if _is_token(token):
            seed_for("material", token, where_scope=scope,
                     where_source=scope_source)

    for mid, material_edges in edges.items():
        for edge in material_edges:
            no_unsourced(edge, REFERENCE_PRODUCED, f"edge<{mid}>")

    materials = {}
    for mid in seeds:
        material = _material_record(seeds[mid], edges.get(mid, []), evidence,
                                    disk, provider=provider, digest=digest)
        # The object that read this manifest is one of the material's sources
        # whether or not an edge was found: a `files.backfilled` name is the
        # manifest's own evidence that it handled something. A zero-edge
        # material with no source would be a material nobody found, which is
        # the one thing this module may not report.
        if record.get("path"):
            material["sources"] = sorted(set(material["sources"])
                                         | {record.get("path")})
        materials[mid] = material
    mine = set(materials)
    own = list(materials.values())
    collisions = _collisions(record, messages, evidence, edges, mine)
    discovery = discovery_coverage(messages, shape=record.get("shape"))
    material_half = material_coverage(own, policy_row, object_key=key,
                                      discovery_state=discovery["state"])
    if record.get("role") == "link":
        # A link object is a capture object in its own right, and its `media`
        # are materials OF the chat that expanded it. Keying them again here
        # would give one material two ids in two scopes — the §4 failure — so
        # they are counted on the parent and this row says so. Without the
        # sentence, 20 link objects read as 20 sources that hold nothing.
        material_half["why"] = list(material_half["why"]) + [
            "this object is a link the collector expanded: its `media` are "
            "materials of the chat that expanded it and are keyed and counted "
            "there, so a zero here is not a statement that the link holds "
            "nothing"]
    understanding_half = _understanding_half(record.get("path"), understanding, record)
    row = {
        "key": key,
        "identity": _identity_of(record, document),
        "path": record.get("path"),
        "shape": record.get("shape"),
        "role": record.get("role"),
        "scope": scope,
        "state": "READ",
        "rows": len(messages["rows"]),
        "coverage": no_unsourced({
            "message": _message_half(record),
            "discovery": discovery,
            "material": material_half,
            "understanding": understanding_half,
        }, COVERAGE_PRODUCED, "coverage"),
        "materials": sorted(mine),
        "materials_this_scope": sorted(m["material_id"] for m in own),
        "legacy": _legacy_summary(messages, evidence, edges, own),
        "stale_markers": _stale_markers(own),
        "pending_claims": _pending_claims(evidence, disk),
        "unindexed": _unindexed(own),
        "why": _object_why(discovery, material_half, understanding_half),
    }
    return (no_unsourced(row, OBJECT_PRODUCED, "object"), materials, collisions)


def _is_token(value) -> bool:
    """Whether a name is a provider token at all.

    Three of the 115 files under `files/` on this tree carry no token in their
    name. They are bytes somebody put there and no material, so they are
    reported as unregistered without being given a material id they did not
    earn.
    """
    return bool(value) and bool(re.match(r"^(?:img|file)_v\d+_", str(value)))


def _identity_of(record: dict, document: dict) -> dict:
    """The object's own name, as the manifest wrote it."""
    chat = document.get("chat") if isinstance(document.get("chat"), dict) else {}
    return {"name": chat.get("name") or document.get("title"),
            "chat_type": chat.get("chat_type"),
            "external": chat.get("external"),
            "url": document.get("url"),
            "type": document.get("type")}


def _message_half(record: dict) -> dict:
    """`capture_coverage`'s message component, COPIED and attributed.

    Not re-derived: a second answer to "how many messages were written" is the
    failure the two modules were split to prevent, so what this module reports
    for the message half is that module's row with its producer named.
    """
    coverage = (record.get("coverage") or {}).get("components") or {}
    component = coverage.get("messages")
    if component is None:
        return no_unsourced({
            "state": "NOT_APPLICABLE",
            "measured_by": "intake/capture_coverage.py",
            "why": [f"this object is a `{record.get('shape')}`, which carries "
                    f"no message stream; its own `body` component is what "
                    f"covers it"],
        }, MESSAGE_HALF_PRODUCED, "coverage")
    return no_unsourced({
        "state": component["state"],
        "measured_by": "intake/capture_coverage.py",
        "copied": component,
        "why": [f"copied from `capture_coverage`'s `messages` component for "
                f"this object; this module did not measure it"],
    }, MESSAGE_HALF_PRODUCED, "coverage")


def _understanding_half(path, understanding, record: dict) -> dict:
    """The understanding half: the caller's measurement, or the refusal.

    Keyed by the capture object's PATH and not by its source key: the
    measurement comes from `source_waterline`, whose rows carry the path they
    read, and joining two documents on two different coordinates is how 67
    measurements become 67 absences.
    """
    if understanding is None:
        return no_unsourced({
            "state": UNDERSTANDING_NOT_MEASURED,
            "measured_by": "intake/source_waterline.py",
            "why": ["which content was READ is a different question from which "
                    "materials exist, and it is answered by the cards. This "
                    "module did not look, and it will not report an unread "
                    "material as read"],
        }, UNDERSTANDING_HALF_PRODUCED, "coverage")
    measured = understanding.get(path)
    if measured is None:
        return no_unsourced({
            "state": UNDERSTANDING_NOT_MEASURED,
            "measured_by": "intake/source_waterline.py",
            "why": [f"the caller supplied measurements for other sources and "
                    f"none for {path!r}: this is an absent measurement and not "
                    f"an empty one"],
        }, UNDERSTANDING_HALF_PRODUCED, "coverage")
    return no_unsourced({
        "state": measured.get("state") or UNDERSTANDING_NOT_MEASURED,
        "measured_by": "intake/source_waterline.py",
        "copied": measured,
        "why": ["copied verbatim from `source_waterline`'s row for this "
                "object: this module reads materials and does not read cards, "
                "and a material being on disk is not the same finding as its "
                "content having been understood"],
    }, UNDERSTANDING_HALF_PRODUCED, "coverage")


def _legacy_summary(messages: dict, evidence: dict, edges: dict,
                    own: list[dict]) -> dict:
    """What the legacy map says, and the two things it structurally cannot.

    §4: 旧碰撞记录不丢，不静默挑最后一个附件给consumer. So this reports the
    map's own size beside the measured one, and names every message where the
    two disagree instead of resolving the disagreement.
    """
    attachment_edges = {mid for mid, material_edges in edges.items()
                        if any(edge["producer"] == "MESSAGE_CONTENT"
                               for edge in material_edges)}
    return no_unsourced({
        "role": "NAVIGATION_ONLY",
        "entries": len(evidence["legacy_by_message"]),
        "backfilled": len(evidence["backfilled"]),
        "image_keys": len(evidence["image_keys"]),
        "materials_from_messages": len(attachment_edges),
        "materials_in_this_object": len(own),
        "pending_text": evidence["pending_text"],
        "why": [
            "the map is one entry per message, so it cannot be the material "
            "denominator: §4 makes it navigation only",
            "a material is registered without its suffix and lands with it, so "
            "joining on the raw name is how a 58-entry map looks like a 0-entry "
            "one — this module joins on the token",
            f"measured on this object: {len(attachment_edges)} material(s) came "
            f"from the messages and {len(evidence['legacy_by_message'])} "
            f"message(s) have a map entry",
        ],
    }, LEGACY_PRODUCED, "legacy")


def _collisions(record: dict, messages: dict, evidence: dict, edges: dict,
                mine: set) -> list[dict]:
    """Messages where the map's answer and the measured edges disagree.

    Two shapes, and both are reported rather than resolved: the map names a
    material the message's own content does not, and the content names
    materials the map does not. §4 says not to silently pick the last
    attachment for a consumer, so a consumer reading this gets both lists.
    """
    row_by_id = {row["message_id"]: row for row in messages["rows"]}
    measured: dict = {}
    for mid in mine:
        for edge in edges.get(mid, []):
            if edge["producer"] != "MESSAGE_CONTENT" or not edge["message_id"]:
                continue
            measured.setdefault(edge["message_id"], set()).add(edge["value"])
    out: list[dict] = []
    for message_id, entry in evidence["legacy_by_message"].items():
        row = row_by_id.get(message_id)
        values = measured.get(message_id, set())
        key = entry.get("file_key")
        if row is None:
            out.append({"message_id": message_id,
                        "state": "LEGACY_NAMES_A_MESSAGE_THIS_CAPTURE_OMITS",
                        "legacy": sorted({str(key)}), "measured": sorted(values),
                        "why": "the map has an entry for a message this "
                               "capture's own messages file does not hold"})
            continue
        missing_from_content = sorted(v for v in {str(key)} if v not in values)
        missing_from_map = sorted(v for v in values if v != str(key))
        if missing_from_content or missing_from_map:
            out.append({"message_id": message_id,
                        "state": "LEGACY_MAP_AND_CONTENT_DISAGREE",
                        "legacy": sorted({str(key)}),
                        "measured": sorted(values),
                        "legacy_names_no_form_found": missing_from_content,
                        "content_names_the_map_does_not": missing_from_map,
                        "row_state": row_state(row),
                        "why": "one entry per message against this many "
                               "references: the map is a single key and the "
                               "message is not"})
    return out


def _object_why(discovery: dict, material_half: dict, understanding: dict) -> list[str]:
    why = []
    if discovery["state"] != "COMPLETE":
        why.append(f"discovery coverage is {discovery['state']}")
    if material_half["state"] != "COMPLETE":
        why.append(f"material coverage is {material_half['state']}")
    if understanding["state"] == UNDERSTANDING_NOT_MEASURED:
        why.append("understanding coverage was not measured by the caller")
    return why


# ------------------------------------------------------------- the document --
def _policy_index(policy) -> dict:
    """`source_policy.resolve`'s document, indexed by the key on its own rows.

    The index is built HERE rather than by the caller because the key lives at
    `row["identity"]["key"]` and not at `row["object"]` — the latter is the
    manifest's path, and a caller who indexed on it would build 67 rows that
    join to nothing, which is exactly what this module did the first time it
    was pointed at the live tree. One place, so there is one way to get it
    wrong and it is testable.
    """
    if not policy:
        return {}
    rows = policy.get("rows") if isinstance(policy, dict) else None
    return {row["identity"]["key"]: row for row in (rows or [])
            if isinstance(row.get("identity"), dict)
            and row["identity"].get("key")}


def _policy_join(policy, index: dict, rows: list[dict]) -> dict:
    """How the policy document and this reading joined, and what did not.

    A half that is `UNMEASURABLE` everywhere is unremarkable on its own; a
    join that reached nothing is the reason, and it is reported here so a
    reader can tell "nobody passed a policy" from "the policy was passed and
    the keys disagree".
    """
    if policy is None:
        return no_unsourced({
            "state": "NOT_PASSED", "rows": 0, "joined": 0, "unjoined": [],
            "why": ["no `source_policy` document was passed, so every material "
                    "half is UNMEASURABLE and none of them is a pass"],
        }, POLICY_JOIN_PRODUCED, "policy")
    keys = [row["key"] for row in rows if row.get("key")]
    unjoined = sorted(set(index) - set(keys))
    return no_unsourced({
        "state": "READ", "rows": len(index),
        "joined": len(set(index) & set(keys)),
        "unjoined": unjoined[:50],
        "unjoined_total": len(unjoined),
        "why": ([] if not unjoined else
                [f"{len(unjoined)} policy row(s) name a source this reading did "
                 f"not reach: a policy for a source nobody measured is not a "
                 f"pass for it"]),
    }, POLICY_JOIN_PRODUCED, "policy")


# ------------------------------------------------------------- the document --
def read_root(records_root: Path | str, objects: list[dict], *,
              policy: dict | None = None, understanding: dict | None = None,
              digest: bool = False) -> dict:
    """Every material this capture tree refers to, and the four coverages.

    `objects` is `capture_coverage.read_root(records)["objects"]`, and `policy`
    is `source_policy.resolve(records_root, objects)` — the whole document,
    not an index, because the index has one correct shape and this module
    builds it. It is handed in rather than re-walked so the denominator is one
    number: two walks of one tree are two denominators, and the one that
    differs is the one a reader will believe.
    """
    root = raw_root(records_root)
    if not root.is_dir():
        raise Refused(
            f"{root} is not a directory: there is no capture tree to read. An "
            f"absent root is not an empty one, and reporting zero materials "
            f"here would say the captures refer to nothing when nothing was "
            f"looked at")
    policies = _policy_index(policy)
    rows: list[dict] = []
    materials: dict = {}
    collisions: list[dict] = []
    for record in objects:
        path = root / str(record.get("path", ""))
        directory = path.parent
        try:
            document = read_manifest(path)
        except Malformed as exc:
            rows.append(no_unsourced({
                "key": None, "identity": {"name": None}, "path": record.get("path"),
                "shape": record.get("shape"), "role": record.get("role"),
                "scope": None, "state": "UNREADABLE", "rows": 0,
                "coverage": {}, "materials": [], "materials_this_scope": [],
                "legacy": {}, "stale_markers": [], "pending_claims": [],
                "unindexed": [], "why": [str(exc)],
            }, OBJECT_PRODUCED, "object"))
            continue
        object_row, found, object_collisions = build_object(
            record, document, directory,
            policy_row=policies.get(source_key(record, document)),
            understanding=understanding, digest=digest)
        rows.append(object_row)
        collisions.extend(object_collisions)
        for mid, material in found.items():
            if mid in materials:
                merged = materials[mid]
                merged["sources"] = sorted(set(merged["sources"])
                                           | set(material["sources"]))
                merged["edges"] += material["edges"]
                continue
            materials[mid] = material
    document = no_unsourced({
        "schema": SCHEMA, "root": str(root), "objects": rows,
        "materials": materials, "collisions": collisions,
        "policy": _policy_join(policy, policies, rows),
    }, DOCUMENT_PRODUCED, "document")
    document["summary"] = summarize(document)
    return document


def summarize(document: dict) -> dict:
    """Counts per axis, and never one number over four questions."""
    by_discovery: dict = {}
    by_material: dict = {}
    by_understanding: dict = {}
    refusals = 0
    unreadable = 0
    for row in document["objects"]:
        if row["state"] != "READ":
            unreadable += 1
            continue
        coverage = row["coverage"]
        for name, bucket in (("discovery", by_discovery),
                             ("material", by_material),
                             ("understanding", by_understanding)):
            half = coverage[name]
            bucket[half["state"]] = bucket.get(half["state"], 0) + 1
            if half.get("refusal"):
                refusals += 1
    materials = document["materials"]
    return no_unsourced({
        "objects": len(document["objects"]),
        "objects_unreadable": unreadable,
        "by_discovery_state": by_discovery,
        "by_material_state": by_material,
        "by_understanding_state": by_understanding,
        "refusals": refusals,
        "materials": len(materials),
        "materials_landed": _count(list(materials.values()), "LANDED"),
        "materials_absent": _count(list(materials.values()), "ABSENT"),
        "materials_with_no_edge": len([m for m in materials.values()
                                       if not m["edges"]]),
        "materials_from_several_sources": len([m for m in materials.values()
                                               if len(m["sources"]) > 1]),
        "materials_redacted_identity": len(
            [m for m in materials.values() if m["identity_state"] == "REDACTED"]),
        "collisions": len(document["collisions"]),
        "stale_markers": len(_stale_markers(list(materials.values()))),
        "unindexed": len(_unindexed(list(materials.values()))),
        "declared_bytes_disagree": _count_disagree(list(materials.values())),
        "policy": document["policy"]["state"],
        "policy_joined": document["policy"]["joined"],
        "pending_claims": _pending_claims_count(document["objects"]),
    }, SUMMARY_PRODUCED, "summary")


def _pending_claims_count(rows: list[dict]) -> dict:
    """The pending sentences, counted by whether anything can be reconciled."""
    out: dict = {}
    for row in rows:
        for claim in row.get("pending_claims") or []:
            out[claim["state"]] = out.get(claim["state"], 0) + 1
    return out


SUMMARY_PRODUCED = {
    "objects": "the capture objects this reading was handed",
    "objects_unreadable": "objects whose own manifest could not be read",
    "by_discovery_state": "objects counted by their discovery half's state",
    "by_material_state": "objects counted by their material half's state",
    "by_understanding_state": "objects counted by their understanding half",
    "refusals": "halves that could not be decided, each naming why",
    "materials": "distinct materials across the whole tree",
    "materials_landed": "materials whose bytes are on disk",
    "materials_absent": "materials with no bytes on disk",
    "materials_with_no_edge": "materials a manifest claims and no message "
                              "reference reaches — our discovery is the "
                              "incomplete side of those, not the collector's",
    "materials_from_several_sources": "materials more than one capture object "
                                      "found, which §4 says is the normal case",
    "materials_redacted_identity": "materials whose only name was replaced by "
                                    "the capture pipeline's redaction marker, so "
                                    "they can never land. Counted apart from "
                                    "`materials_absent`, which is a fetch that "
                                    "has not happened yet",
    "collisions": "messages where the legacy map and the measured edges differ",
    "stale_markers": "declared pending and landed anyway",
    "unindexed": "on disk with no manifest list naming it",
    "declared_bytes_disagree": "declared size and measured size both present "
                               "and different",
    "policy": "the policy join's state, so `UNMEASURABLE` everywhere is never "
              "read as a measurement",
    "policy_joined": "policy rows that name a source this reading also reached",
    "pending_claims": "the collector's pending sentences, counted by whether "
                      "anything on the manifest lets them be reconciled",
}

PENDING_CLAIM_PRODUCED = {
    "text": "the collector's `files.pending` sentence, verbatim and never "
            "parsed into a count",
    "names_materials": "the tokens the sentence itself carries, if any. Empty "
                       "is the common case on this tree and is the finding",
    "files_on_disk": "how many files sit under this capture's `files/`",
    "files_without_a_token": "those whose name carries no provider token — a "
                             "`.gitkeep` is not material, and counting it "
                             "silently would inflate the claim",
    "state": "NAMES_A_MATERIAL when the sentence can be reconciled against "
             "bytes, NAMES_NO_MATERIAL when nothing identifies what is missing",
    "why": "one sentence, and the one that decides who has to act",
}

LEGACY_PRODUCED = {
    "role": "always NAVIGATION_ONLY — the statement, not a note",
    "entries": "messages the map has an entry for",
    "backfilled": "names the collector listed as backfilled",
    "image_keys": "names the collector listed as pending",
    "materials_from_messages": "materials the message contents themselves name",
    "materials_in_this_object": "materials this object's evidence reached",
    "pending_text": "the collector's `files.pending` sentence, verbatim",
    "why": "what the map can and cannot be used for, with this object's own "
           "numbers",
}


def render(document: dict) -> list[str]:
    """The human face: what is not complete, per object, and why."""
    lines = [f"materials   root {document['root']}",
             f"            {document['summary']['objects']} object(s), "
             f"{document['summary']['materials']} material(s), "
             f"{document['summary']['collisions']} collision(s)"]
    for row in document["objects"]:
        if row["state"] != "READ":
            lines.append(f"  UNREADABLE  {row['path']}")
            for why in row["why"]:
                lines.append(f"              {why}")
            continue
        coverage = row["coverage"]
        name = row["identity"].get("name") or row["identity"].get("url") or "?"
        lines.append(
            f"  {row['key']}")
        lines.append(
            f"    {name}")
        for peer in ("message", "discovery", "material", "understanding"):
            half = coverage[peer]
            lines.append(f"    {peer:14s} {half['state']:22s} "
                         f"{half['measured_by']}")
            for why in half["why"]:
                lines.append(f"        · {why}")
            if half.get("refusal"):
                lines.append(f"        refusal: "
                             f"{half['refusal']['what_would_decide_it']}")
        for gap in coverage["discovery"].get("gaps", [])[:6]:
            lines.append(f"      gap {gap['msg_type']:14s} "
                         f"pos {gap['message_position']}  {gap['state']}")
        lines.append(f"    legacy        {row['legacy']['entries']} entry(s), "
                     f"{row['legacy']['materials_from_messages']} material(s) "
                     f"from the messages themselves")
        for marker in row["stale_markers"]:
            lines.append(f"    stale marker  {marker['material_id']} — "
                         f"{marker['why']}; reconcile, do not download again")
        for claim in row["pending_claims"]:
            lines.append(f"    pending claim {claim['state']} — "
                         f"{claim['text']!r} ({claim['files_on_disk']} file(s) "
                         f"on disk)")
        for entry in row["unindexed"]:
            lines.append(f"    unindexed     {entry['material_id']} "
                         f"({entry['bytes']} bytes) — no list names it")
    if document["collisions"]:
        lines.append("  collisions")
        for collision in document["collisions"][:20]:
            lines.append(f"    {collision['state']}  "
                         f"{collision['why']}")
    lines.append(f"  summary {document['summary']}")
    return lines


def _count(materials: list[dict], state: str) -> int:
    return len([m for m in materials if m["landing"]["state"] == state])


def _count_registered(materials: list[dict]) -> int:
    return len([m for m in materials
                if m["landing"]["state"] == "ABSENT"
                and "DECLARED_BACKFILLED" in m["registry"]["states"]])


def _count_referenced(materials: list[dict]) -> int:
    return len([m for m in materials
                if m["landing"]["state"] == "ABSENT" and m["edges"]])


def _count_disagree(materials: list[dict]) -> int:
    return len([m for m in materials if m["bytes_agree"] is False])


def _stale_markers(materials: list[dict]) -> list[dict]:
    """Declared pending, and landed anyway. §10's reconcile-don't-redownload."""
    return [{"material_id": material["material_id"],
             "why": "declared pending by a manifest and present on disk"}
            for material in materials
            if material["landing"]["state"] == "LANDED"
            and "DECLARED_PENDING" in material["registry"]["states"]]


def _pending_claims(evidence: dict, disk: dict) -> list[dict]:
    """The collector's `files.pending` sentence, and what it does not say.

    On this tree the sentence is prose — `3 张图片未下载` — and it names no
    token, so it cannot be attached to a material and cannot be reconciled by
    recomputing a set difference. §10's row `现存文件pending旧 → 核字节/关联后
    修索引，不重复下` is answered where the sentence does name something; where
    it names nothing the finding is that a claim about three files exists and
    nothing identifies which three, which is the thing a reader must fix by
    hand. The tokens the sentence does carry are reported so the two cases are
    told apart instead of merged into one.
    """
    text = evidence.get("pending_text")
    if not text:
        return []
    named: list[str] = []
    for form in REFERENCE_FORMS.values():
        named.extend(match.group(1) for match in
                     form["pattern"].finditer(str(text)))
    landed = sorted(disk)
    unnamed = [name for name in landed if not _is_token(_token_of(name))]
    return [no_unsourced({
        "text": str(text),
        "names_materials": sorted(set(named)),
        "files_on_disk": len(landed),
        "files_without_a_token": unnamed,
        "state": ("NAMES_A_MATERIAL" if named else "NAMES_NO_MATERIAL"),
        "why": ("the sentence names a token, so what it claims can be checked "
                "against the bytes"
                if named else
                f"the sentence names no token and {len(landed)} file(s) are on "
                f"disk under this capture"
                + (f", {len(unnamed)} of which carry no token at all"
                   if unnamed else "")
                + ": nobody can tell from this manifest which materials are "
                  "still missing, and no download should be started until a "
                  "person reconciles it"),
    }, PENDING_CLAIM_PRODUCED, "pending_claim")]



def _unindexed(materials: list[dict]) -> list[dict]:
    """Landed, with no manifest list claiming it — the index is behind."""
    return [{"material_id": material["material_id"],
             "bytes": material["landing"]["bytes"],
             "why": "bytes on disk that no list on the manifest names"}
            for material in materials
            if material["landing"]["state"] == "LANDED"
            and not material["registry"]["states"]]
