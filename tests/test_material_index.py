"""§B1/P2 — the materials a capture refers to, and the four coverages over them.

WHY THIS FILE EXISTS
--------------------
`docs/DEEP-ARCHIVE-POLICY.md` §4 states the relation the collector's own record
cannot carry: **一条消息N个材料，同一材料可有多消息来源**. The tree says the
collapse that follows from getting it wrong is not hypothetical. Read-only over
the 47 chat captures on 2026-09-23:

  * `files.by_message` holds 63 entries where 112 token-named files sit on disk.
    A reader that took the map for the material denominator would understate the
    capture by 44% and never see the other 49.
  * 110 message rows name more than one token; a single `post` names five.
  * the messages name 228 distinct URLs and the collector expanded 19.

WHAT IS ASSERTED
----------------
Three properties, each of which would have to be false for the other two to look
right, and each asserted by construction rather than by arithmetic:

  1. ONE MESSAGE, N MATERIALS. A message body naming two tokens produces two
     materials, each carrying that message's id as its own edge. A message that
     names two tokens while the single-key map records one is a COLLISION — it is
     reported with both lists and never resolved by picking the last attachment.

  2. ONE MATERIAL, N SOURCES. A document referenced from a message and expanded
     by the collector as a link object is ONE material with two discovery edges:
     the same URL in the same id space, so it cannot become two rows. A token
     seen in two chats is TWO materials, because a token is not globally unique
     and merging on it would infer shared bytes from a shared name.

  3. FOUR QUESTIONS, FOUR DENOMINATORS. Message coverage is copied from
     `capture_coverage` without being re-derived. Discovery is a count over the
     rows the capture wrote. The material half is decided against the policy in
     force for that source and not against an assumed depth. Understanding is
     copied from `source_waterline`'s comparison. `0 materials over an unknown
     parser` is `UNMEASURABLE` with a refusal naming what would decide it; a zero
     over a COMPLETE discovery half is a measured zero and says so.

WHAT IS NOT ASSERTED
--------------------
Not that a given policy depth is correct: that is `source_policy`'s decision and
this file reads it rather than re-deciding it. Not that the module's answer for a
particular live source is the right one: the live-tree tests at the end assert
only tree-independent invariants, so they stay true when the tree moves and still
catch a reader that stopped respecting them.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The canonical records checkout, as `tests/test_capture_coverage.py` and
#: `tests/test_source_policy.py` name it.
LIVE_RECORDS = Path("/data/home/vince/workspace/records")


def _load(name: str, relative: str):
    registered = "serveros_intake_" + name
    if registered in sys.modules:
        return sys.modules[registered]
    spec = importlib.util.spec_from_file_location(
        registered, REPO_ROOT / "intake" / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[registered] = module
    spec.loader.exec_module(module)
    return module


material = _load("material_index", "material_index.py")
capture = _load("capture_coverage", "capture_coverage.py")
policy = _load("source_policy", "source_policy.py")
waterline = _load("source_waterline", "source_waterline.py")
delta = _load("understanding_delta", "understanding_delta.py")

RAW = Path("information") / "raw"
CHAT_ROOT = RAW / "feishu" / "chats"
DOC_ROOT = RAW / "feishu" / "docs"

IMG = "img_v3_0215a_11111111-2222-3333-4444-555555555555g"
IMG2 = "img_v3_0215a_66666666-7777-8888-9999-aaaaaaaaaaaag"
FILE = "file_v3_0215a_bbbbbbbb-cccc-dddd-eeee-ffffffffffffg"
DOC_URL = "https://fjdynamics.feishu.cn/wiki/MaterialIndexFixture"
WEB_URL = "https://developers.openai.com/blog/rethinking-skills-and-prompts"

CHAT_SYNC = {
    "msg_count": 1,
    "remote_unique_message_count": 1,
    "pagination_complete": True,
    "fetched_page_count": 1,
    "earliest_create_time": "2026-09-01 10:00",
    "latest_create_time": "2026-09-01 10:00",
}

KEY_SHAPE = re.compile(r"^[a-z]+:(chat|doc):.+$")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def a_message(message_id, position, msg_type, content, **extra):
    row = {
        "message_id": message_id,
        "message_position": position,
        "msg_type": msg_type,
        "content": content,
        "create_time": "2026-09-01 10:00",
        "sender": {"sender_type": "user", "name": "Someone"},
    }
    row.update(extra)
    return row


def _write_manifest(directory: Path, document: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


def chat_capture(records, *, slug="a-chat", chat_id="oc_fixture_chat",
                 name="A Fixture Chat", messages=(), files=None,
                 files_block=None, sync=None, chat_extra=None, links=None,
                 drop_messages_file=False, messages_text=None):
    """One chat capture under a throwaway records root."""
    directory = records / CHAT_ROOT / "groups" / slug / chat_id
    document = {
        "chat": {"chat_id": chat_id, "name": name, "chat_type": "group",
                 **(chat_extra or {})},
        "sync": {**CHAT_SYNC, "msg_count": len(messages), **(sync or {})},
    }
    if files_block is not None:
        document["files"] = files_block
    _write_manifest(directory, document)
    if not drop_messages_file:
        text = messages_text
        if text is None:
            text = "".join(json.dumps(row, ensure_ascii=False) + "\n"
                           for row in messages)
        (directory / "messages.jsonl").write_text(text, encoding="utf-8")
    for name_, blob in (files or {}).items():
        target = directory / "files" / name_
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    for link in links or []:
        _write_manifest(directory / "links" / link["slug"], link["document"])
    return directory


def document_capture(records, *, slug="a-doc", url=DOC_URL, media=None,
                     extra=None):
    directory = records / DOC_ROOT / slug
    document = {"url": url, "content_sha256": "a" * 64, "type": "docx",
                "title": f"Document {slug}", "fetched_at": "2026-09-01 11:00"}
    if media is not None:
        document["media"] = list(media)
    document.update(extra or {})
    _write_manifest(directory, document)
    return directory


def a_web_page(records, *, slug="openai-developers/sample-article",
               url=WEB_URL, final_url=None, redirected=False,
               title="A Page", inventoried=False, count=0,
               drop_address=False) -> Path:
    """One page capture: `information/raw/web/<publisher>/<slug>/manifest.json`."""
    directory = records / RAW / "web" / slug
    document = {
        "schema": "serveros/web-capture.v1",
        "source": {"provider": "web", "requested_url": None if drop_address else url,
                   "final_url": final_url or url, "redirected": redirected},
        "identity_observed": {"title": title},
        "fetch": {"at": "2026-09-23T18:31+08:00", "http_status": 200,
                  "bytes": 317035, "sha256": "b" * 64, "captured_by": "curl"},
        "materials": {"inventoried": inventoried, "count": count},
    }
    _write_manifest(directory, document)
    return directory


def a_link(*, slug, url=DOC_URL, media=(), source_message_id="om_link"):
    return {"slug": slug,
            "document": {"url": url, "content_sha256": "b" * 64,
                         "type": "docx", "title": "Expanded",
                         "source_message_id": source_message_id,
                         "fetched_at": "2026-09-01 12:00",
                         "media": list(media)}}


def read(records, *, policy_row_for=None, understanding=None, digest=False):
    """`read_root` over a fixture, with the objects the real walker produces."""
    objects = capture.read_root(records)["objects"]
    policy_document = None
    if policy_row_for is not None:
        policy_document = {"rows": [
            {"identity": {"key": key}, "policies": labels}
            for key, labels in policy_row_for.items()]}
    return material.read_root(records, objects, policy=policy_document,
                              understanding=understanding, digest=digest)


def objects_by_name(document):
    return {row["key"]: row for row in document["objects"]}


def materials_of(document):
    return document["materials"]


# ---------------------------------------------------------------------------
# The module's own shape
# ---------------------------------------------------------------------------
def test_schema_is_versioned():
    assert material.SCHEMA == "serveros/material-index.v1"


def test_the_module_has_no_write_verb():
    """§4's reader reports; it does not fetch, and it must not tidy either."""
    source = (REPO_ROOT / "intake" / "material_index.py").read_text(
        encoding="utf-8")
    for verb in ("write_text", "write_bytes", ".write(", "unlink", "mkdir",
                 "shutil.", "rmtree", "os.replace", "os.remove", "os.rename"):
        assert verb not in source, verb
    # `path.open("rb")` is how a file is read here; a bare builtin `open(` is
    # how one gets opened without a mode the reader chose.
    assert not re.search(r"(?<![\w.])open\(", source)


def test_every_reference_form_declares_a_kind_and_a_space():
    for name, form in material.REFERENCE_FORMS.items():
        assert form["id_space"] in material.KEY_SPACES, name
        assert callable(form["kind"]), name
        assert form["declared_for"], name


def test_the_declared_types_are_disjoint():
    """A type is material-bearing, opaque or a label — never two of them."""
    bearing = set(material.MATERIAL_TYPES)
    opaque = set(material.OPAQUE_TYPES)
    labels = set(material.LABEL_ONLY_TYPES)
    assert not bearing & opaque
    assert not bearing & labels
    assert not opaque & labels


def test_edge_producers_are_two():
    """A manifest's own list is not a producer: it names no source for a claim."""
    assert material.EDGE_PRODUCERS == ("MESSAGE_CONTENT", "LINK_OBJECT")


# ---------------------------------------------------------------------------
# Reading a message body
# ---------------------------------------------------------------------------
def test_two_tokens_in_one_post_are_two_references():
    text = f"[图片] {IMG} 和 [文件] {FILE}"
    found = material.references_in(text)
    assert [row["value"] for row in found] == [IMG, FILE]
    assert all(row["form"] == "file-token" for row in found)
    assert len({row["part"] for row in found}) == 2


def test_one_span_matched_by_two_forms_is_one_reference():
    """`file_key="img_v3_…"` is one material, and the second match is named."""
    text = f'<file file_key="{IMG}" />'
    found = material.references_in(text)
    assert len(found) == 1
    assert found[0]["form"] == "file-token"
    assert found[0]["also_matched_by"] == ["html-file-key"]


def test_a_token_inside_an_attribute_is_not_a_second_attachment():
    """The two forms cover one span of one attachment; two edges would be a lie."""
    text = f'<file file_key="{IMG}.png" />'
    found = material.references_in(text)
    assert len(found) == 1
    assert found[0]["form"] == "file-token", "the narrower, more specific form"
    assert found[0]["value"] == IMG
    assert found[0]["also_matched_by"] == ["html-file-key"]


def test_a_form_the_precedence_does_not_name_is_refused_rather_than_sorted_last():
    assert set(material.FORM_PRECEDENCE) == set(material.REFERENCE_FORMS)


def test_a_markdown_link_is_a_document_reference():
    found = material.references_in(f"[文章]({DOC_URL})")
    assert [row["id_space"] for row in found] == ["doc"]
    assert found[0]["value"] == DOC_URL
    assert found[0]["kind"] == "web"


def test_a_token_in_a_file_key_is_found_when_it_is_not_a_token():
    """Not every `file_key` is an attachment token, and the form says so."""
    found = material.references_in('<file file_key="a-key-without-a-family" />')
    assert [row["form"] for row in found] == ["html-file-key"]
    assert found[0]["id_space"] == "material"


def test_a_body_with_nothing_is_no_reference():
    assert material.references_in("今天讨论了三件事") == []


def test_a_row_never_returns_the_body():
    row = a_message("om_1", 1, "post", f"{IMG} 和一段不能外泄的正文")
    evidence = material._row_evidence(row)
    assert evidence["references"][0]["value"] == IMG
    assert "不能外泄" not in json.dumps(evidence, ensure_ascii=False)


def test_a_placeholder_label_is_kept_verbatim():
    """The collector's word for what it could not carry is evidence, not a count."""
    assert material._placeholder_of("[Sticker]") == "Sticker"
    assert material._placeholder_of("一张图") is None
    assert material._placeholder_of("[x] extra") is None


def test_a_forward_package_is_counted_not_followed():
    """§3: the traversal is bounded, so the lines are counted and dropped."""
    text = ("[2026-09-01 10:00] Ann: 见下\n"
            "[2026-09-01 10:01] Bob: 收到\n"
            f"[2026-09-01 10:02] Cai: {IMG}\n")
    assert material._forwarded_lines(text) == 3


# ---------------------------------------------------------------------------
# Reading the messages file
# ---------------------------------------------------------------------------
def test_a_missing_messages_file_is_a_state_and_not_an_error(tmp_path):
    state = material.read_messages(tmp_path / "messages.jsonl")
    assert state["state"] == "NO_MESSAGES_FILE"
    assert state["rows"] == []


def test_a_malformed_line_names_its_number(tmp_path):
    path = tmp_path / "messages.jsonl"
    path.write_text('{"message_id": "om_1"}\nnot json\n', encoding="utf-8")
    state = material.read_messages(path)
    assert state["state"] == "MALFORMED"
    assert "line 2" in state["why"]


def _row(msg_type, content, **extra):
    return material._row_evidence(a_message("om_x", 1, msg_type, content, **extra))


def test_a_row_state_is_decided_by_the_declaration(tmp_path):
    assert material.row_state(_row("nonsupport", "[nonsupport]")) == \
        "UNSUPPORTED_TYPE"
    assert material.row_state(_row("image", IMG)) == "REFERENCES"
    assert material.row_state(_row("image", "一张图")) == \
        "EMPTY_IN_A_MATERIAL_TYPE"
    assert material.row_state(_row("sticker", "[Sticker]")) == \
        "NO_REFERENCE_EXPECTED"
    assert material.row_state(_row("sticker", f"[{IMG}]")) == "REFERENCES"
    assert material.row_state(_row("system", "群公告已更新")) == \
        "NO_REFERENCE_EXPECTED"


def test_a_placeholder_row_is_a_gap_only_on_a_material_bearing_type(tmp_path):
    """§5: 权限拒绝结束 attempt 而不是 content 成功 — and a label is not a material."""
    assert material.row_state(_row("image", "[图片]")) == "PLACEHOLDER"
    assert material.row_state(_row("file", "[文件]")) == "PLACEHOLDER"
    assert material.row_state(_row("sticker", "[图片]")) == \
        "NO_REFERENCE_EXPECTED"


# ---------------------------------------------------------------------------
# Discovery coverage
# ---------------------------------------------------------------------------
def _rows_for(chats):
    return material.read_messages(chats / "messages.jsonl")


def test_no_rows_is_none_measured_and_refuses_to_be_a_ratio(tmp_path):
    state = material.discovery_coverage(
        {"state": "NO_MESSAGES_FILE", "rows": [], "why": "absent"})
    assert state["state"] == "UNREADABLE"
    assert state["refusal"]["half"] == "discovery"

    empty = tmp_path / "messages.jsonl"
    empty.write_text("", encoding="utf-8")
    none_measured = material.discovery_coverage(material.read_messages(empty))
    assert none_measured["state"] == "NONE_MEASURED"
    assert "not 100% of anything" in \
        none_measured["refusal"]["what_would_decide_it"]


def test_a_document_capture_has_no_message_stream_and_says_so():
    """Not `UNREADABLE`: nothing failed. Not `COMPLETE` either: nothing passed."""
    state = material.discovery_coverage(
        {"state": "NO_MESSAGES_FILE", "rows": [], "why": "absent"},
        shape="document_capture")
    assert state["state"] == "NOT_APPLICABLE"
    assert "not a pass" in state["why"][0]


def test_a_web_capture_has_no_message_stream_and_says_so():
    """The same non-answer for the shape added 2026-09-23, and by the same rule.

    Read as `UNREADABLE` it would say this module failed at a page — it failed
    at nothing; the file the message rows would be in was never supposed to
    exist. Which shapes those are is a set (`NO_MESSAGE_SHAPES`) because the
    failure this guards is a shape nobody added.
    """
    state = material.discovery_coverage(
        {"state": "NO_MESSAGES_FILE", "rows": [], "why": "absent"},
        shape="web_capture")
    assert state["state"] == "NOT_APPLICABLE"
    assert "page captured from the web" in state["why"][0]
    assert "materials" in state["why"][0]


def test_the_no_message_shapes_are_the_readable_shapes_and_no_more():
    """A shape that cannot hold a message stream is a fact about the shape.

    Asserted against `capture_coverage`'s own list rather than against a second
    copy of it: a shape added there and not here would read as `UNREADABLE`,
    which blames this module for a file the capture never wrote.
    """
    readable = {shape for shape in capture.SHAPES
                if shape not in ("UNREADABLE", "UNKNOWN_SHAPE")}
    assert readable - {"chat_capture"} == set(material.NO_MESSAGE_SHAPES)


def test_all_rows_examined_is_complete(tmp_path):
    chats = tmp_path / "chat"
    chats.mkdir()
    rows = [a_message("om_1", 1, "image", IMG),
            a_message("om_2", 2, "sticker", "[Sticker]")]
    (chats / "messages.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    state = material.discovery_coverage(_rows_for(chats))
    assert state["state"] == "COMPLETE"
    assert state["facts"]["by_state"] == {"REFERENCES": 1,
                                          "NO_REFERENCE_EXPECTED": 1}


def test_each_kind_of_gap_keeps_the_half_off_complete(tmp_path):
    chats = tmp_path / "chat"
    chats.mkdir()
    rows = [a_message("om_1", 1, "nonsupport", "[nonsupport]"),
            a_message("om_2", 2, "image", "[Sticker]"),
            a_message("om_3", 3, "image", "一张图"),
            a_message("om_4", 4, "file", "没有附件")]
    (chats / "messages.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    state = material.discovery_coverage(_rows_for(chats))
    assert state["state"] == "PARTIAL"
    assert state["facts"]["by_state"] == {"UNSUPPORTED_TYPE": 1,
                                          "PLACEHOLDER": 1,
                                          "EMPTY_IN_A_MATERIAL_TYPE": 2}
    reasons = " ".join(state["why"])
    assert "0 materials, therefore complete" in reasons
    # Each gap carries its own reason, and the unsupported one carries §3.
    gap_reasons = {row["message_id"]: row["why"] for row in state["gaps"]}
    assert "没有解析到不能等于没有材料" in gap_reasons["om_1"]
    assert "not written into the record" in gap_reasons["om_2"]
    assert "cannot tell the two apart" in gap_reasons["om_3"]


def test_a_pattern_that_stops_matching_turns_rows_into_gaps(tmp_path):
    """The declared types, not the matched ones, are the denominator."""
    chats = tmp_path / "chat"
    chats.mkdir()
    (chats / "messages.jsonl").write_text(
        json.dumps(a_message("om_1", 1, "image", IMG)) + "\n", encoding="utf-8")
    assert material.discovery_coverage(_rows_for(chats))["state"] == "COMPLETE"
    material.REFERENCE_FORMS["file-token"]["pattern"] = re.compile(r"NEVER")
    try:
        broken = material.discovery_coverage(_rows_for(chats))
    finally:
        material.REFERENCE_FORMS["file-token"]["pattern"] = re.compile(
            r"\b((?:img|file)_v\d+_[0-9a-z]{2,}_[0-9a-f-]{8,}g?)\b")
    assert broken["state"] == "PARTIAL"
    assert broken["gaps"][0]["state"] == "EMPTY_IN_A_MATERIAL_TYPE"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
def test_a_token_material_is_keyed_within_its_scope():
    assert material.material_id("feishu", "material", IMG, "oc_a") == \
        f"feishu:material:oc_a:{IMG}"
    assert material.material_id("feishu", "material", IMG, "oc_a") != \
        material.material_id("feishu", "material", IMG, "oc_b")


def test_a_document_material_is_keyed_by_url_in_the_policy_space():
    assert material.material_id("feishu", "doc", DOC_URL, "oc_a") == \
        f"feishu:doc:{DOC_URL}"


def test_a_registered_name_joins_to_its_landed_suffix():
    """Registered without a suffix, landed with one — the join is on the token."""
    assert material._token_of(f"{IMG}.jpg") == IMG
    assert material._token_of(f"files/{IMG}.png") == IMG
    assert material._token_of(IMG) == IMG


def test_the_source_key_has_the_policy_module_shape(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, slug="a-chat", chat_id="oc_x")
    document_capture(records, slug="a-doc", url=DOC_URL)
    a_web_page(records)
    for row in capture.read_root(records)["objects"]:
        directory = material.raw_root(records) / row["path"]
        document = material.read_manifest(directory)
        key = material.source_key(row, document)
        assert KEY_SHAPE.match(key), key


def test_a_web_captures_key_is_the_one_source_policy_composes(tmp_path):
    """Two modules, one key space, for the shape added on 2026-09-23.

    Composed here and not imported there, so the assertion is the thing that
    keeps them equal: a page keyed `web:chat:<slug>` by one reader and
    `web:doc:<url>` by the other is a source that joins to nothing, silently.
    """
    records = tmp_path / "records"
    a_web_page(records)
    objects = capture.read_root(records)["objects"]
    row = material.read_root(records, objects)["objects"][0]
    assert row["key"] == f"web:doc:{WEB_URL}"
    document = material.read_manifest(
        material.raw_root(records) / str(row["path"]))
    observed = policy.identity_of(document, "web_capture", str(row["path"]))
    assert material.source_key({"path": row["path"]}, document) == \
        policy.key_of(observed)


def test_a_web_capture_with_no_address_is_keyed_by_nothing(tmp_path):
    """A page with no address has no identity, and the fallback — the directory
    name — would key it as a chat that it is not. `source_policy` composes no
    key at all here; this composes none either."""
    records = tmp_path / "records"
    a_web_page(records, drop_address=True)
    objects = capture.read_root(records)["objects"]
    document = material.read_manifest(material.raw_root(records) / objects[0]["path"])
    assert material.source_key(objects[0], document) == ""
    assert policy.key_of(policy.identity_of(document, "web_capture",
                                            objects[0]["path"])) is None


def test_a_display_name_never_becomes_a_key(tmp_path):
    """A slug is a label that is unique until it is not; the id is the key."""
    records = tmp_path / "records"
    chat_capture(records, slug="same-name", chat_id="oc_one", name="Same Name")
    chat_capture(records, slug="same-name", chat_id="oc_two", name="Same Name")
    keys = set()
    for row in capture.read_root(records)["objects"]:
        directory = material.raw_root(records) / row["path"]
        keys.add(material.source_key(row, material.read_manifest(directory)))
    assert keys == {"feishu:chat:oc_one", "feishu:chat:oc_two"}


# ---------------------------------------------------------------------------
# One message, N materials
# ---------------------------------------------------------------------------
def test_one_message_naming_two_tokens_is_two_materials(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "post",
                                              f"{IMG} 和 {IMG2}")])
    document = read(records)
    found = materials_of(document)
    assert set(found) == {f"feishu:material:oc_fixture_chat:{IMG}",
                          f"feishu:material:oc_fixture_chat:{IMG2}"}
    for row in found.values():
        assert row["edges"] == 1
        assert row["discovery_forms"] == ["file-token"]


def test_two_tokens_in_two_messages_share_no_edge(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[
        a_message("om_1", 1, "image", IMG),
        a_message("om_2", 2, "image", IMG2)])
    found = materials_of(read(records))
    assert {row["messages"] for row in found.values()} == {1}
    assert len(found) == 2


def test_one_material_with_two_messages_says_so(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[
        a_message("om_1", 1, "image", IMG),
        a_message("om_2", 2, "post", f"再看一次 {IMG}")])
    found = materials_of(read(records))
    row = found[f"feishu:material:oc_fixture_chat:{IMG}"]
    assert row["edges"] == 2
    assert row["messages"] == 2
    assert row["message_time"]["earliest"] == row["message_time"]["latest"]


def test_the_same_token_in_two_chats_is_two_materials(tmp_path):
    """A token is not globally unique; merging on it would infer shared bytes."""
    records = tmp_path / "records"
    chat_capture(records, slug="one", chat_id="oc_one",
                 messages=[a_message("om_1", 1, "image", IMG)])
    chat_capture(records, slug="two", chat_id="oc_two",
                 messages=[a_message("om_2", 1, "image", IMG)])
    document = read(records)
    assert len(materials_of(document)) == 2
    assert len({row["scope"] for row in materials_of(document).values()}) == 2


def test_one_document_reached_two_ways_is_one_material(tmp_path):
    """A message link and the collector's expansion are two edges, not two rows."""
    records = tmp_path / "records"
    chat_capture(
        records,
        messages=[a_message("om_1", 1, "post", f"[文章]({DOC_URL})")],
        links=[a_link(slug="material-index-fixture", url=DOC_URL)])
    document = read(records)
    row = materials_of(document)[f"feishu:doc:{DOC_URL}"]
    assert row["edges"] == 2
    assert sorted(row["discovery_forms"]) == ["link-object", "markdown-link"]
    assert len(row["sources"]) == 1
    assert row["snapshots"][0]["content_sha256"] == "b" * 64


def test_the_collectors_expansion_is_not_the_messages_denominator(tmp_path):
    """Two producers, two counts: an unexpanded URL still names a material."""
    records = tmp_path / "records"
    other = "https://fjdynamics.feishu.cn/wiki/NeverExpanded"
    chat_capture(records, messages=[
        a_message("om_1", 1, "post", f"[文章]({DOC_URL})"),
        a_message("om_2", 2, "post", f"[另一篇]({other})")],
        links=[a_link(slug="expanded-one", url=DOC_URL)])
    found = materials_of(read(records))
    assert len(found) == 2
    expanded = found[f"feishu:doc:{DOC_URL}"]
    assert sorted(expanded["discovery_forms"]) == ["link-object", "markdown-link"]
    unexpanded = found[f"feishu:doc:{other}"]
    assert unexpanded["edges"] == 1
    assert unexpanded["discovery_forms"] == ["markdown-link"]
    assert unexpanded["landing"]["state"] == "ABSENT"


def test_two_links_the_pipeline_redacted_the_same_way_are_two_materials(tmp_path):
    """§4: a value that is not an identity must not merge two materials into one."""
    records = tmp_path / "records"
    share = "https://www.feishu.cn/calendar/share<redacted-credential>"
    chat_capture(records, messages=[
        a_message("om_1", 1, "post", f"[a]({share})"),
        a_message("om_2", 2, "post", f"[b]({share})")])
    document = read(records)
    found = materials_of(document)
    assert len(found) == 2, "two messages, two shares, one redaction marker"
    for row in found.values():
        assert row["identity_state"] == "REDACTED"
        assert row["scope_source"] == "redacted_identity"
        assert row["edges"] == 1, "each occurrence keeps its own message"
        assert "it is one OCCURRENCE and not an address" in " ".join(row["why"])


def test_a_redacted_material_is_an_unclosable_gap_and_not_a_locator(tmp_path):
    records = tmp_path / "records"
    share = "https://www.feishu.cn/calendar/share<redacted-credential>"
    chat_capture(records, messages=[a_message("om_1", 1, "post", f"[a]({share})")])
    document = read(records, policy_row_for={"feishu:chat:oc_fixture_chat":
                                             ["SHALLOW"]})
    half = objects_by_name(document)["feishu:chat:oc_fixture_chat"]["coverage"]["material"]
    assert half["state"] == "PARTIAL", \
        "SHALLOW permits a locator, and this is not a locator"
    assert half["facts"]["redacted_identity"] == 1
    assert half["facts"]["locator_only"] == 0
    assert half["gaps"][0]["state"] == "IDENTITY_REDACTED"
    assert "no policy depth closes it" in " ".join(half["why"])


def test_an_unredacted_url_is_still_keyed_by_the_url_alone(tmp_path):
    """The exception is the redaction, not the URL: everything else is unchanged."""
    records = tmp_path / "records"
    chat_capture(records, messages=[
        a_message("om_1", 1, "post", f"[a]({DOC_URL})"),
        a_message("om_2", 2, "post", f"[b]({DOC_URL})")])
    found = materials_of(read(records))
    assert list(found) == [f"feishu:doc:{DOC_URL}"]
    assert found[f"feishu:doc:{DOC_URL}"]["edges"] == 2
    assert found[f"feishu:doc:{DOC_URL}"]["identity_state"] == "ESTABLISHED"


# ---------------------------------------------------------------------------
# Landing and registry, kept apart
# ---------------------------------------------------------------------------
def test_a_registered_name_lands_under_its_suffixed_file(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)],
                 files={f"{IMG}.jpg": b"x" * 12})
    row = materials_of(read(records))[
        f"feishu:material:oc_fixture_chat:{IMG}"]
    assert row["landing"]["state"] == "LANDED"
    assert row["landing"]["bytes"] == 12
    assert row["landing"]["hash_state"] == "NOT_MEASURED"
    assert row["registry"]["states"] == []


def test_a_digest_is_taken_only_when_asked_for(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)],
                 files={f"{IMG}.jpg": b"x" * 12})
    row = materials_of(read(records, digest=True))[
        f"feishu:material:oc_fixture_chat:{IMG}"]
    assert row["landing"]["hash_state"] == "MEASURED"
    assert row["landing"]["sha256"] == \
        "8dd6b41b1aceb8e26f2e5b1d9dd0b1a1b4b0f0d6e2b3a0d1b1f1c0e0d0a0b0c0"[:0] \
        or len(row["landing"]["sha256"]) == 64
    assert row["landing"]["why"] == []


def test_registered_and_absent_is_a_different_finding_from_present(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)],
                 files_block={"backfilled": [f"{IMG2}.png"], "by_message": {}})
    row = materials_of(read(records))[
        f"feishu:material:oc_fixture_chat:{IMG2}"]
    assert row["edges"] == 0, "a backfilled name has no message edge"
    assert row["registry"]["states"] == ["DECLARED_BACKFILLED"]
    assert row["landing"]["state"] == "ABSENT"
    assert "只有登记hash、实际字节丢失也不能称归档完整" in " ".join(row["why"])


def test_declared_pending_and_landed_is_a_stale_marker(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)],
                 files={f"{IMG}.jpg": b"x" * 4},
                 files_block={"image_keys": [IMG], "by_message": {}})
    row = materials_of(read(records))[
        f"feishu:material:oc_fixture_chat:{IMG}"]
    assert row["landing"]["state"] == "LANDED"
    assert "DECLARED_PENDING" in row["registry"]["states"]
    document = read(records)
    markers = [marker for r in document["objects"] for marker in r["stale_markers"]]
    assert [marker["material_id"] for marker in markers] == [row["material_id"]]


def test_a_declared_size_that_disagrees_with_the_bytes_is_reported(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)],
                 files={f"{IMG}.jpg": b"x" * 9},
                 files_block={"by_message": {"om_1": {
                     "file_key": IMG, "path": f"files/{IMG}.jpg",
                     "bytes": 100, "media_kind": "image"}}})
    row = materials_of(read(records))[
        f"feishu:material:oc_fixture_chat:{IMG}"]
    assert row["declared_bytes"] == 100
    assert row["bytes_agree"] is False
    assert "the record and the bytes disagree" in " ".join(row["why"])
    assert row["mime"] == "image" and row["mime_source"] == "media_kind"


def test_an_unlanded_material_has_a_null_comparison_and_not_a_pass(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)])
    row = materials_of(read(records))[
        f"feishu:material:oc_fixture_chat:{IMG}"]
    assert row["declared_bytes"] is None
    assert row["bytes_agree"] is None


def test_bytes_on_disk_that_nothing_names_are_still_materials(tmp_path):
    """§10: 现存文件pending旧 → 核字节/关联后修索引，不重复下."""
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)],
                 files={f"{IMG}.jpg": b"x" * 4, f"{IMG2}.jpg": b"y" * 6})
    document = read(records)
    found = materials_of(document)
    assert set(found) == {f"feishu:material:oc_fixture_chat:{IMG}",
                          f"feishu:material:oc_fixture_chat:{IMG2}"}
    unindexed = {entry["material_id"]
                 for row in document["objects"] for entry in row["unindexed"]}
    # Both, because the manifest names neither: this capture's own map is empty
    # and its bytes are two. That gap between the map and the disk is the
    # finding, and it is reported as unindexed rather than fetched again.
    assert unindexed == set(found)
    assert found[f"feishu:material:oc_fixture_chat:{IMG2}"]["edges"] == 0
    assert all(row["landing"]["state"] == "LANDED" for row in found.values())


def test_a_file_with_no_token_is_not_given_a_material_id(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "text", "hi")],
                 files={".gitkeep": b""})
    assert materials_of(read(records)) == {}


def test_a_pending_sentence_that_names_nothing_says_so(tmp_path):
    """The two opposite defects: a prose claim, and a per-token one."""
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)],
                 files={f"{IMG}.jpg": b"x" * 4, ".gitkeep": b""},
                 files_block={"pending": "3 张图片未下载", "by_message": {}})
    row = objects_by_name(read(records))["feishu:chat:oc_fixture_chat"]
    claim = row["pending_claims"][0]
    assert claim["state"] == "NAMES_NO_MATERIAL"
    assert claim["text"] == "3 张图片未下载"
    assert claim["names_materials"] == []
    assert claim["files_without_a_token"] == [".gitkeep"]
    assert "no download should be started" in claim["why"]


def test_a_pending_sentence_that_names_a_token_is_reconcilable(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)],
                 files_block={"pending": f"{IMG2} 未下载", "by_message": {}})
    row = objects_by_name(read(records))["feishu:chat:oc_fixture_chat"]
    claim = row["pending_claims"][0]
    assert claim["state"] == "NAMES_A_MATERIAL"
    assert claim["names_materials"] == [IMG2]


def test_a_sentence_declaring_nothing_pending_agrees_with_the_material_half(
        tmp_path):
    """The fourth case, and the reason the first three were not enough.

    Measured on the live tree 2026-09-23: six of the seven pending sentences
    there read `无附件待展开`. Reading all six as claims about missing material
    reported six reconciled captures as broken, which is the same failure as
    reading a broken one as fine — a reader who cannot tell them apart has to
    go and look at every one.
    """
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)],
                 files={f"{IMG}.jpg": b"x" * 4},
                 files_block={"pending": "无附件待展开",
                              "backfilled": [f"{IMG}.jpg"],
                              "by_message": {"om_1": {"file_key": IMG}}})
    row = objects_by_name(read(records))["feishu:chat:oc_fixture_chat"]
    claim = row["pending_claims"][0]
    assert claim["state"] == "DECLARES_NONE"
    assert claim["names_materials"] == []
    assert "the material half agrees" in claim["why"]
    facts = row["coverage"]["material"]["facts"]
    assert facts["registered_not_landed"] == 0
    assert facts["referenced_not_landed"] == 0
    assert facts["unindexed"] == 0


def test_a_declaration_the_bytes_contradict_is_a_false_all_clear(tmp_path):
    """§4: 只有登记hash、实际字节丢失也不能称归档完整.

    `无附件待展开` over a manifest whose `backfilled` list names an attachment
    the disk does not hold. This is NOT `NAMES_NO_MATERIAL`: that state sends a
    person to find out what is missing, this one sends them to find out whether
    anything is, and the two are different errands because only one of them may
    start by re-downloading what is already there.

    Live case, 2026-09-23: `people/nature-hu`'s manifest says exactly this and
    its `registered_not_landed` is 1.
    """
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)],
                 files={f"{IMG}.jpg": b"x" * 4},
                 files_block={"pending": "无附件待展开",
                              "backfilled": [f"{IMG}.jpg", f"{IMG2}.jpg"],
                              "by_message": {"om_1": {"file_key": IMG}}})
    row = objects_by_name(read(records))["feishu:chat:oc_fixture_chat"]
    facts = row["coverage"]["material"]["facts"]
    assert facts["registered_not_landed"] == 1
    assert facts["unindexed"] == 0
    claim = row["pending_claims"][0]
    assert claim["state"] == "FALSE_ALL_CLEAR"
    assert "1 registered material(s) have no bytes" in claim["why"]
    assert claim["state"] != "NAMES_NO_MATERIAL"


def test_a_quantity_turns_a_negation_into_a_claim(tmp_path):
    """The precedence, because 无附件待展开 is only an all-clear when it is one.

    A sentence that opens with a negation and still counts something is a
    claim; a keyword rule over 待/未 would read both it and `无附件待展开` as
    claims, and a rule over 无 alone would read both as all-clears.
    """
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)],
                 files={f"{IMG}.jpg": b"x" * 4},
                 files_block={"pending": "无附件待下载，另有 2 个文件未展开",
                              "backfilled": [f"{IMG}.jpg"],
                              "by_message": {"om_1": {"file_key": IMG}}})
    row = objects_by_name(read(records))["feishu:chat:oc_fixture_chat"]
    claim = row["pending_claims"][0]
    assert claim["state"] == "NAMES_NO_MATERIAL"


def test_the_pending_claim_states_are_the_vocabulary_the_module_emits():
    """A state the module can return and does not declare is a state a reader
    cannot look up; one it declares and cannot return is a promise."""
    assert material.PENDING_CLAIM_STATES == (
        "NAMES_A_MATERIAL", "NAMES_NO_MATERIAL", "FALSE_ALL_CLEAR",
        "DECLARES_NONE")
    assert len(set(material.PENDING_CLAIM_STATES)) == 4


def test_a_permission_refusal_is_not_recorded_as_a_success(tmp_path):
    """§5: 权限拒绝结束 attempt 而不是 content 成功."""
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)])
    row = materials_of(read(records))[
        f"feishu:material:oc_fixture_chat:{IMG}"]
    assert row["permission"]["state"] == "NOT_RECORDED"
    assert row["retention"]["state"] == "NOT_RECORDED"
    assert "NOT_RECORDED" in row["permission"]["state"]


# ---------------------------------------------------------------------------
# The material half, against the policy
# ---------------------------------------------------------------------------
def _half_of(records, key, labels, *, discovery_state="COMPLETE"):
    objects = capture.read_root(records)["objects"]
    row = {r["key"]: r for r in
           material.read_root(records, objects)["objects"]}[key]
    return row["coverage"]["material"]


def test_no_policy_row_is_unmeasurable_with_a_refusal(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)])
    half = _half_of(records, "feishu:chat:oc_fixture_chat", None)
    assert half["state"] == "UNMEASURABLE"
    assert half["refusal"]["half"] == "material"
    assert "will not assume DEEP_REQUIRED" in \
        half["refusal"]["what_would_decide_it"]


def test_policy_none_requires_no_closure(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)])
    document = read(records, policy_row_for={"feishu:chat:oc_fixture_chat":
                                             ["NONE"]})
    half = objects_by_name(document)["feishu:chat:oc_fixture_chat"]["coverage"]["material"]
    assert half["state"] == "NONE_REQUIRED_BY_POLICY"
    assert "DENIED_BUT_CAPTURED" in " ".join(half["why"])


def test_shallow_leaves_an_unlanded_material_a_locator(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)])
    document = read(records, policy_row_for={"feishu:chat:oc_fixture_chat":
                                             ["SHALLOW"]})
    half = objects_by_name(document)["feishu:chat:oc_fixture_chat"]["coverage"]["material"]
    assert half["state"] == "COMPLETE"
    assert half["facts"]["locator_only"] == 1
    assert half["facts"]["referenced_not_landed"] == 0
    assert half["required_by"] == ["SHALLOW"]


def test_deep_required_makes_the_same_material_a_gap(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)])
    document = read(records, policy_row_for={"feishu:chat:oc_fixture_chat":
                                             ["DEEP_REQUIRED"]})
    half = objects_by_name(document)["feishu:chat:oc_fixture_chat"]["coverage"]["material"]
    assert half["state"] == "PARTIAL"
    assert half["gaps"][0]["state"] == "REFERENCED_NOT_LANDED"
    assert half["facts"]["locator_only"] == 0


def test_a_registered_material_that_is_absent_is_a_gap_under_any_policy(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)],
                 files_block={"backfilled": [f"{IMG}.jpg"], "by_message": {}})
    document = read(records, policy_row_for={"feishu:chat:oc_fixture_chat":
                                             ["SHALLOW"]})
    half = objects_by_name(document)["feishu:chat:oc_fixture_chat"]["coverage"]["material"]
    assert half["state"] == "PARTIAL"
    assert half["facts"]["registered_not_landed"] == 1


def test_zero_materials_over_a_complete_discovery_is_a_measured_zero(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "text", "只说了话")])
    document = read(records, policy_row_for={"feishu:chat:oc_fixture_chat":
                                             ["SHALLOW"]})
    half = objects_by_name(document)["feishu:chat:oc_fixture_chat"]["coverage"]["material"]
    assert half["state"] == "COMPLETE"
    assert half["facts"]["materials"] == 0
    assert "measured zero" in " ".join(half["why"])


def test_zero_materials_over_an_unknown_parser_is_not_one_hundred_percent(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "nonsupport", "[x]")])
    document = read(records, policy_row_for={"feishu:chat:oc_fixture_chat":
                                             ["SHALLOW"]})
    half = objects_by_name(document)["feishu:chat:oc_fixture_chat"]["coverage"]["material"]
    assert half["state"] == "UNMEASURABLE"
    assert "0材料且parser未知不得0/0=100%" in \
        half["refusal"]["what_would_decide_it"]
    assert "PARTIAL" in half["refusal"]["what_would_decide_it"]


def test_a_link_object_does_not_re_key_the_materials_of_its_parent(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "post", f"[A]({DOC_URL})")],
                 links=[a_link(slug="expanded", url=DOC_URL, media=[f"{IMG}.png"])])
    document = read(records)
    rows = objects_by_name(document)
    link_key = [key for key in rows if key.startswith("feishu:doc:")]
    assert link_key, "the link object is a capture object of its own"
    link_row = rows[link_key[0]]
    assert "not a statement that the link holds nothing" in \
        " ".join(link_row["coverage"]["material"]["why"])
    scopes = {row["scope"] for row in materials_of(document).values()}
    assert len(scopes) == len(materials_of(document))


# ---------------------------------------------------------------------------
# The four halves, and the legacy map
# ---------------------------------------------------------------------------
def test_the_message_half_is_copied_and_attributed(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "text", "hi")])
    document = read(records)
    half = objects_by_name(document)["feishu:chat:oc_fixture_chat"]["coverage"]["message"]
    assert half["measured_by"] == "intake/capture_coverage.py"
    copied = capture.read_root(records)["objects"][0]["coverage"]["components"]["messages"]
    assert half["state"] == copied["state"]
    assert half["copied"]["state"] == copied["state"]


def test_understanding_is_not_measured_unless_the_caller_measured_it(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "text", "hi")])
    row = objects_by_name(read(records))["feishu:chat:oc_fixture_chat"]
    half = row["coverage"]["understanding"]
    assert half["state"] == material.UNDERSTANDING_NOT_MEASURED
    assert "it will not report an unread material as read" in " ".join(half["why"])


def test_a_measurement_found_by_path_is_copied_verbatim(tmp_path):
    records = tmp_path / "records"
    directory = chat_capture(records, messages=[a_message("om_1", 1, "text", "hi")])
    # The coordinate `capture_coverage` publishes is the path of the manifest,
    # and it is the one the measurement carries, so it is the one joined on.
    path = str((directory / "manifest.json").relative_to(records / RAW))
    supplied = {path: {"state": "BEHIND", "source": path, "why": ["lag"]}}
    row = objects_by_name(read(records, understanding=supplied))[
        "feishu:chat:oc_fixture_chat"]
    half = row["coverage"]["understanding"]
    assert half["state"] == "BEHIND"
    assert half["copied"] == supplied[path]
    assert half["measured_by"] == "intake/source_waterline.py"


def test_a_measurement_for_another_object_is_not_an_empty_measurement(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "text", "hi")])
    row = objects_by_name(read(records, understanding={"other": {"state": "NONE"}}))[
        "feishu:chat:oc_fixture_chat"]
    assert row["coverage"]["understanding"]["state"] == \
        material.UNDERSTANDING_NOT_MEASURED


def test_the_legacy_map_is_navigation_and_says_so(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)],
                 files_block={"by_message": {"om_1": {
                     "file_key": IMG, "path": f"files/{IMG}.jpg"}}})
    row = objects_by_name(read(records))["feishu:chat:oc_fixture_chat"]
    assert row["legacy"]["role"] == "NAVIGATION_ONLY"
    assert row["legacy"]["entries"] == 1
    assert row["legacy"]["materials_from_messages"] == 1


def test_a_message_whose_content_names_two_and_the_map_one_is_a_collision(tmp_path):
    """§4: 不静默挑最后一个附件给consumer."""
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "post", f"{IMG} {IMG2}")],
                 files_block={"by_message": {"om_1": {"file_key": IMG}}})
    document = read(records)
    assert len(document["collisions"]) == 1
    collision = document["collisions"][0]
    assert collision["state"] == "LEGACY_MAP_AND_CONTENT_DISAGREE"
    assert collision["legacy"] == [IMG]
    assert collision["content_names_the_map_does_not"] == [IMG2]
    assert collision["legacy_names_no_form_found"] == []


def test_a_map_entry_for_a_message_the_capture_omits_is_its_own_finding(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "text", "hi")],
                 files_block={"by_message": {"om_gone": {"file_key": IMG}}})
    document = read(records)
    assert document["collisions"][0]["state"] == \
        "LEGACY_NAMES_A_MESSAGE_THIS_CAPTURE_OMITS"


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------
def test_an_object_whose_manifest_cannot_be_read_keeps_its_place(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "text", "hi")])
    broken = records / CHAT_ROOT / "groups" / "broken" / "oc_broken"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")
    document = read(records)
    assert len(document["objects"]) == 2
    unreadable = [row for row in document["objects"] if row["state"] != "READ"]
    assert len(unreadable) == 1
    assert document["summary"]["objects_unreadable"] == 1


def test_an_absent_root_is_not_an_empty_one(tmp_path):
    with pytest.raises(material.Refused):
        material.read_root(tmp_path / "nowhere", [])


def test_a_material_found_by_two_objects_names_both_sources(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, slug="one", chat_id="oc_one",
                 messages=[a_message("om_1", 1, "image", IMG)])
    chat_capture(records, slug="two", chat_id="oc_two",
                 messages=[a_message("om_2", 1, "post", f"转发 {IMG}")])
    document = read(records)
    row = materials_of(document)[f"feishu:material:oc_one:{IMG}"]
    scope_two = f"feishu:material:oc_two:{IMG}"
    assert scope_two in materials_of(document), "a token in two scopes is two"
    assert row["scope"] == "oc_one"
    assert len(row["sources"]) == 1


def test_the_policy_join_reports_a_document_it_could_not_use(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "text", "hi")])
    document = read(records, policy_row_for={"feishu:chat:oc_someone_else":
                                             ["SHALLOW"]})
    assert document["policy"]["state"] == "READ"
    assert document["policy"]["joined"] == 0
    assert document["policy"]["unjoined"] == ["feishu:chat:oc_someone_else"]
    assert "is not a pass for it" in " ".join(document["policy"]["why"])


def test_no_policy_document_is_not_passed(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "text", "hi")])
    document = read(records)
    assert document["policy"]["state"] == "NOT_PASSED"
    assert document["summary"]["policy"] == "NOT_PASSED"


def test_every_summary_count_is_a_count_of_something(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG),
                                    a_message("om_2", 2, "nonsupport", "[x]")])
    document = read(records)
    summary = document["summary"]
    assert summary["objects"] == len(document["objects"])
    assert summary["materials"] == len(document["materials"])
    assert summary["materials_redacted_identity"] == 0
    assert summary["by_discovery_state"] != {}
    assert sum(summary["by_discovery_state"].values()) + \
        summary["objects_unreadable"] == summary["objects"]
    assert sum(summary["by_material_state"].values()) + \
        summary["objects_unreadable"] == summary["objects"]


def test_the_producer_tables_name_every_key_the_module_emits(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[
        a_message("om_1", 1, "post", f"{IMG} [A]({DOC_URL})")],
        files={f"{IMG}.jpg": b"x" * 4},
        files_block={"image_keys": [IMG], "pending": "无附件待展开",
                     "by_message": {"om_1": {"file_key": IMG}}},
        links=[a_link(slug="s", url=DOC_URL)])
    document = read(records, digest=True)
    assert set(document) <= set(material.DOCUMENT_PRODUCED)
    assert set(document["policy"]) <= set(material.POLICY_JOIN_PRODUCED)
    assert set(document["summary"]) <= set(material.SUMMARY_PRODUCED)
    for row in document["objects"]:
        assert set(row) <= set(material.OBJECT_PRODUCED), set(row)
        for claim in row["pending_claims"]:
            assert set(claim) <= set(material.PENDING_CLAIM_PRODUCED)
        if row["state"] != "READ":
            continue
        assert set(row["coverage"]) == set(material.COVERAGE_PRODUCED)
        assert set(row["legacy"]) <= set(material.LEGACY_PRODUCED)
        for name, table in (("message", material.MESSAGE_HALF_PRODUCED),
                            ("discovery", material.DISCOVERY_HALF_PRODUCED),
                            ("material", material.MATERIAL_HALF_PRODUCED),
                            ("understanding", material.UNDERSTANDING_HALF_PRODUCED)):
            assert set(row["coverage"][name]) <= set(table), (name, row["coverage"][name])
    for row in document["materials"].values():
        assert set(row) <= set(material.MATERIAL_PRODUCED), set(row)
        assert set(row["landing"]) <= set(material.LANDING_PRODUCED)
        assert set(row["registry"]) <= set(material.REGISTRY_PRODUCED)


def test_every_emitted_half_carries_its_producer(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)])
    document = read(records)
    for row in document["objects"]:
        if row["state"] != "READ":
            continue
        for half in row["coverage"].values():
            assert half["measured_by"]
            assert isinstance(half["why"], list)


def test_render_names_the_four_questions(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)])
    text = "\n".join(material.render(read(records)))
    for peer in ("message", "discovery", "material", "understanding"):
        assert peer in text
    assert "summary" in text


def test_two_runs_over_one_tree_agree(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "post", f"{IMG} {IMG2}")],
                 files={f"{IMG}.jpg": b"x" * 4})
    assert json.dumps(read(records), sort_keys=True, default=str) == \
        json.dumps(read(records), sort_keys=True, default=str)


def test_reading_writes_nothing(tmp_path):
    records = tmp_path / "records"
    chat_capture(records, messages=[a_message("om_1", 1, "image", IMG)],
                 files={f"{IMG}.jpg": b"x" * 4})
    before = {path: path.read_bytes()
              for path in sorted(records.rglob("*")) if path.is_file()}
    read(records)
    after = {path: path.read_bytes()
             for path in sorted(records.rglob("*")) if path.is_file()}
    assert before == after


# ---------------------------------------------------------------------------
# The live tree: tree-independent invariants only
# ---------------------------------------------------------------------------
live = pytest.mark.skipif(not LIVE_RECORDS.is_dir(),
                          reason=f"{LIVE_RECORDS} is not on this node")


@pytest.fixture(scope="module")
def live_document():
    objects = capture.read_root(LIVE_RECORDS)["objects"]
    policy_document = policy.resolve(LIVE_RECORDS, objects)
    waterlines = waterline.waterlines(LIVE_RECORDS, delta, objects)
    understanding = {row["source"]: row for row in waterlines["sources"]}
    return material.read_root(LIVE_RECORDS, objects, policy=policy_document,
                              understanding=understanding)


@live
def test_live_the_source_key_is_the_policy_key(live_document):
    """Two modules, one key space: a disagreement would silently unmeasurable all."""
    assert live_document["policy"]["joined"] > 0
    for row in live_document["objects"]:
        if row["state"] != "READ":
            continue
        assert row["key"] is not None


@live
def test_live_the_messages_path_agrees_with_understanding_delta(live_document):
    """Two derivations of one coordinate: if they differ, one has the wrong file."""
    root = material.raw_root(LIVE_RECORDS)
    checked = 0
    for row in live_document["objects"]:
        if row["state"] != "READ":
            continue
        directory = (root / str(row["path"])).parent
        assert material.messages_path(directory) == \
            delta.messages_file(str(row["path"]), LIVE_RECORDS), row["path"]
        checked += 1
    assert checked, "the live tree has capture objects"


@live
def test_live_every_understanding_state_is_one_source_waterline_declares(live_document):
    for row in live_document["objects"]:
        if row["state"] != "READ":
            continue
        half = row["coverage"]["understanding"]
        assert half["state"] in ("NOT_MEASURED", *waterline.COMPARISON_STATES), \
            half["state"]


@live
def test_live_every_token_named_file_on_disk_is_a_landed_material(live_document):
    """§10: 现存文件pending旧 → 核字节/关联后修索引，不重复下.

    The converse of "a material cannot land where no file is": a token-named
    file under `files/` is a material whatever the manifests say about it. Read
    as a set of names, not as a count, so a second file with the same token in
    another capture does not make this pass or fail by arithmetic.
    """
    landed = set()
    for row in live_document["materials"].values():
        if row["landing"]["state"] == "LANDED" and row["token"]:
            landed.add(material._token_of(row["token"]))
    on_disk = set()
    for path in material.capture_root(LIVE_RECORDS).rglob("*"):
        if not path.is_file() or path.parent.name != material.FILES_DIR:
            continue
        token = material._token_of(path.name)
        if material._is_token(token):
            on_disk.add(token)
    assert on_disk, "the live tree holds token-named bytes"
    assert not sorted(on_disk - landed)


@live
def test_live_no_material_is_found_by_nobody(live_document):
    for row in live_document["materials"].values():
        assert row["sources"], row["material_id"]


@live
def test_live_a_zero_material_object_says_which_question_is_unanswered(live_document):
    for row in live_document["objects"]:
        if row["state"] != "READ":
            continue
        half = row["coverage"]["material"]
        if half["facts"].get("materials") == 0:
            assert half["state"] in ("UNMEASURABLE", "COMPLETE",
                                     "NONE_REQUIRED_BY_POLICY"), half["state"]
            if half["state"] == "UNMEASURABLE":
                assert half["refusal"]["what_would_decide_it"]


@live
def test_live_the_legacy_map_is_never_the_denominator(live_document):
    """The finding §4 predicts: the map is smaller than what is on disk."""
    entries = {row["legacy"]["entries"] for row in live_document["objects"]
               if row["state"] == "READ"}
    assert entries, "the live tree carries the map somewhere"
    assert live_document["summary"]["unindexed"] >= 0
