"""Capture policy and materialized coverage, read as two answers and not one.

WHY THIS FILE EXISTS
--------------------
Neither half of `intake/capture_coverage.py` was previously observable. Read on
2026-09-22, `grep -rn "pagination_complete\\|msg_count" --include=*.py` over the
fabric tree returned nothing: the collectors wrote 66 manifests under
`records/information/raw/` and no ServerOS-side reader had ever opened one. So
every property below is asserted here for the first time, and the four that are
individually easy to get wrong are each given their own class:

  * an absence is not a value. `pagination_complete` has three states on this
    node, not two — `true`, `false`, and *the collector did not record it* — and
    collapsing the third into either of the others is the failure the module was
    written for. `sync.mode` is missing from 37 of 47 manifests and must not
    acquire a default.
  * a component's shortfall is not the object's verdict. One manifest can be
    fully paginated and still have attachments the collector declared pending.
    There is no object-level verdict to assert on, and its absence is asserted.
  * one unreadable manifest must not shrink the denominator. A reader that
    returned 65 objects where 66 exist would understate coverage by exactly the
    amount it failed to look at.
  * the reader reads no clock. Freshness is a different question with a
    different threshold, and a module that answered it would be the authority on
    a fact it does not hold.

THE CAPTURE
-----------
`tests/data/capture-coverage-live-2026-09-22.json`
    `serverosctl information captured --json` over `/data/home/vince/workspace/records`,
    captured 2026-09-22, sha256 of the FULL output
    `7434bee1d2640795b874168368d3338549bd1c1136dab8c8ecd41f01a6fdee09`; the file
    here is trimmed to `path`/`role`/`shape`/`capture`/component `state` and
    `facts`, dropping the `why` prose and the `unstructured` sentences. The trim
    is mechanical, so a test that reads it can still be checked against the
    original by digest. Nothing in it was written by hand.

The capture is dated and the records root is alive. On 2026-09-23 the collector
rewrote one manifest the capture had already read and added one the capture could
not have — a document link under `feishu/chats/people/nature-hu/<chat>/links/`.
The 65 manifests that were neither still read exactly as they did, which is the
measurement that separates the two ways a re-run can differ. So the live-tree
test compares per manifest and directionally rather than by summary total: a
capture landing, or a manifest the collector re-verified, is the TREE advancing;
a captured manifest reading differently without that is the READER moving. Only
the second is a failure. Comparing totals makes the first look like the second,
which is this module's neighbour's whole subject — a total standing in for the
positions underneath it.

The live-tree test does not assert the captured numbers against constants alone:
it re-walks the real records root and asserts the report's denominator equals the
number of `manifest.json` files that are actually there. A fixture that drifted
from the tree would then fail as a drift rather than as an edit.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "runtime" / "cli.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "capture-coverage-live-2026-09-22.json"
LIVE_RECORDS = Path("/data/home/vince/workspace/records")
# The day the capture above was taken, from its own name. A manifest the
# collector re-verified after this day is the tree advancing; one that reads
# differently without that is the reader moving.
CAPTURED_ON = "2026-09-22"


def _load():
    spec = importlib.util.spec_from_file_location(
        "serveros_intake_capture_coverage",
        REPO_ROOT / "intake" / "capture_coverage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = _load()


def cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, cwd=str(REPO_ROOT))


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def chat_manifest(**over) -> dict:
    """A chat capture shaped like the real ones, with only the named fields changed."""
    document = {
        "contact": {"name": "X", "open_id": "ou_x"},
        "chat": {"chat_id": "oc_x", "chat_type": "p2p", "name": "X"},
        "sync": {
            "mode": "incremental",
            "method": "im/chat-messages-list",
            "msg_count": 43,
            "remote_unique_message_count": 43,
            "fetched_page_count": 1,
            "pagination_complete": True,
            "earliest_create_time": "2026-09-11 09:45",
            "latest_create_time": "2026-09-11 10:07",
            "last_sync_at": "2026-09-14",
            "last_verified_at": "2026-09-14T15:45:58",
            "status": "synced",
            "last_message_id": "om_x",
        },
        "note": "a narrative the reader must not project",
    }
    document.update(over)
    return document


def document_manifest(**over) -> dict:
    document = {
        "source_message_id": "om_x",
        "url": "https://example.invalid/doc",
        "type": "docx",
        "title": "T",
        "fetched_at": "2026-09-07",
        "last_verified_at": "2026-09-08T00:00:00",
        "content_sha256": "a" * 64,
        "redaction_applied": False,
    }
    document.update(over)
    return document


@pytest.fixture
def tree(tmp_path):
    """A capture tree a test owns, laid out the way `records/information/raw` is."""
    root = tmp_path / "records" / "information" / "raw" / "feishu" / "chats"
    root.mkdir(parents=True)
    return root


def write(root: Path, relative: str, document) -> Path:
    path = root / relative / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(document, str):
        path.write_text(document, encoding="utf-8")
    else:
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


#: Where a page captured from outside this workspace lands:
#: `information/raw/web/<publisher>/<slug>/manifest.json`. The plane is the
#: first segment, which is also what `material_index.provider_of` reads.
WEB_SLUG = "web/openai-developers/sample-article"


def web_manifest(**over) -> dict:
    """A page capture shaped like the real one, only the named fields changed."""
    document = {
        "schema": "serveros/web-capture.v1",
        "source": {
            "provider": "web",
            "requested_url": "https://example.invalid/article",
            "final_url": "https://example.invalid/article",
            "redirected": False,
        },
        "identity_observed": {"title": "An Article", "author": "A"},
        "fetch": {"at": "2026-09-23T18:31+08:00", "http_status": 200,
                  "bytes": 317035, "sha256": "b" * 64, "captured_by": "curl"},
        "materials": {"inventoried": False, "count": 0},
    }
    document.update(over)
    return document


def write_web(records: Path, relative: str, document) -> Path:
    """The same `write`, rooted at the plane's own root instead of a chat's."""
    return write(records / "information" / "raw", relative, document)


@pytest.fixture
def page(tmp_path):
    """A records root holding one web capture, and nothing of any other shape."""
    tree = tmp_path / "records" / "information" / "raw" / "feishu" / "chats"
    tree.mkdir(parents=True)
    records = tmp_path / "records"
    write_web(records, WEB_SLUG, web_manifest())
    return records


def the_page(records: Path) -> dict:
    records_ = [o for o in cc.read_root(records)["objects"]
                if o["shape"] == "web_capture"]
    assert len(records_) == 1, f"expected one page capture, read {len(records_)}"
    return records_[0]


# --------------------------------------------------------------------------
# an absence is not a value
# --------------------------------------------------------------------------

class TestAnAbsenceIsNotAValue:
    def test_a_recorded_mode_is_reported_and_nothing_is_undeclared(self, tree):
        write(tree, "people/x/oc_x", chat_manifest())
        record = cc.read_root(tree.parents[3])["objects"][0]
        assert record["capture"] == {"mode": "incremental",
                                     "method": "im/chat-messages-list"}
        assert record["capture_undeclared"] == []

    def test_an_absent_mode_is_absent_and_named_rather_than_defaulted(self, tree):
        document = chat_manifest()
        del document["sync"]["mode"]
        write(tree, "people/x/oc_x", document)
        record = cc.read_root(tree.parents[3])["objects"][0]
        assert "mode" not in record["capture"]
        assert [entry["field"] for entry in record["capture_undeclared"]] == ["mode"]
        # The reason travels with the absence, and it names the manifest field
        # rather than this reader's opinion about it.
        assert record["capture_undeclared"][0]["produced_by"] == "sync.mode"
        assert "did not record" in record["capture_undeclared"][0]["why"]

    @pytest.mark.parametrize("value,state", [
        (True, "COMPLETE"),
        (False, "INCOMPLETE"),
        (None, "UNDECIDABLE"),
    ])
    def test_pagination_complete_has_three_states_not_two(self, tree, value, state):
        document = chat_manifest()
        document["sync"]["pagination_complete"] = value
        if value is None:
            del document["sync"]["pagination_complete"]
        write(tree, "people/x/oc_x", document)
        messages = cc.read_root(tree.parents[3])["objects"][0]["coverage"]["components"]["messages"]
        assert messages["state"] == state
        if value is None:
            assert "pagination_complete" not in messages["facts"]

    @pytest.mark.parametrize("value", ["43", True, 43.0, None])
    def test_a_count_that_is_not_an_integer_is_absent_not_a_disagreement(self, tree, value):
        """`"43" != 43` and `True == 1` both turn a readable manifest into a wrong one."""
        document = chat_manifest()
        document["sync"]["msg_count"] = value
        write(tree, "people/x/oc_x", document)
        messages = cc.read_root(tree.parents[3])["objects"][0]["coverage"]["components"]["messages"]
        assert "msg_count" not in messages["facts"]
        assert messages["state"] == "UNDECIDABLE"
        assert not any("source's own count" in reason for reason in messages["why"])
        assert any("no readable `msg_count`" in reason for reason in messages["why"])

    def test_a_real_disagreement_is_incomplete_and_names_both_numbers(self, tree):
        document = chat_manifest()
        document["sync"]["remote_unique_message_count"] = 51
        write(tree, "people/x/oc_x", document)
        messages = cc.read_root(tree.parents[3])["objects"][0]["coverage"]["components"]["messages"]
        assert messages["state"] == "INCOMPLETE"
        assert any("43" in reason and "51" in reason for reason in messages["why"])


# --------------------------------------------------------------------------
# a component's shortfall is not the object's verdict
# --------------------------------------------------------------------------

class TestNoObjectLevelVerdict:
    def test_the_record_carries_no_single_verdict_key(self, tree):
        write(tree, "people/x/oc_x", chat_manifest())
        record = cc.read_root(tree.parents[3])["objects"][0]
        assert "verdict" not in record
        assert "status" not in record["coverage"]
        assert sorted(record["coverage"]["components"]) == ["messages"]

    def test_a_paginated_chat_with_pending_attachments_reports_both(self, tree):
        document = chat_manifest(
            files={"backfilled": [], "pending": "3 张图片未下载"})
        write(tree, "people/x/oc_x", document)
        coverage = cc.read_root(tree.parents[3])["objects"][0]["coverage"]
        assert coverage["components"]["messages"]["state"] == "COMPLETE"
        assert coverage["components"]["attachments"]["state"] == "COMPLETE"
        # The collector's own words are carried beside the components and are
        # marked as prose, because no component above reads them.
        assert coverage["unstructured"]["files_pending"]["text"] == "3 张图片未下载"
        assert "prose" in coverage["unstructured"]["files_pending"]["note"]

    def test_the_narrative_note_is_not_projected(self, tree):
        write(tree, "people/x/oc_x", chat_manifest())
        record = cc.read_root(tree.parents[3])["objects"][0]
        assert "a narrative the reader must not project" not in json.dumps(
            record, ensure_ascii=False)

    def test_the_rollup_counts_per_component_and_not_per_object(self, tree):
        write(tree, "people/x/oc_x", chat_manifest())
        write(tree, "people/x/oc_y", chat_manifest(
            files={"backfilled": [], "pending": "无附件"}))
        summary = cc.read_root(tree.parents[3])["summary"]
        assert summary["denominator"] == 2
        assert summary["components"]["messages"] == {"COMPLETE": 2}
        # Counted over two objects but one component each: an object-level count
        # would have had to collapse them.
        assert summary["components"]["attachments"] == {"COMPLETE": 1}


# --------------------------------------------------------------------------
# one unreadable manifest must not shrink the denominator
# --------------------------------------------------------------------------

class TestTheDenominatorSurvivesABadFile:
    def test_a_malformed_manifest_is_a_record_and_not_an_abort(self, tree):
        write(tree, "people/x/oc_x", chat_manifest())
        write(tree, "people/x/oc_y", "{not json")
        write(tree, "people/x/oc_z", chat_manifest())
        report = cc.read_root(tree.parents[3])
        assert report["summary"]["denominator"] == 3
        assert report["summary"]["by_shape"]["UNREADABLE"] == 1
        assert report["summary"]["by_shape"]["chat_capture"] == 2
        bad = [o for o in report["objects"] if o["shape"] == "UNREADABLE"][0]
        assert "cannot read" in bad["why"]

    def test_a_json_array_is_unreadable_rather_than_an_empty_manifest(self, tree):
        write(tree, "people/x/oc_x", "[1, 2, 3]")
        record = cc.read_root(tree.parents[3])["objects"][0]
        assert record["shape"] == "UNREADABLE"
        assert "list, not a manifest object" in record["why"]

    def test_an_absent_root_refuses_instead_of_reporting_zero(self, tmp_path):
        with pytest.raises(cc.Refused) as caught:
            cc.read_root(tmp_path / "nothing-here")
        assert "not a directory" in str(caught.value)
        assert "not an empty one" in str(caught.value)


# --------------------------------------------------------------------------
# shape from the keys, role from the path, and the disagreement is reported
# --------------------------------------------------------------------------

class TestShapeAndRoleAreSeparateFacts:
    def test_a_default_layout_gives_role_and_shape_that_agree(self, tree):
        write(tree, "people/x/oc_x", chat_manifest())
        write(tree, "people/x/oc_x/links/slug", document_manifest())
        by_role = {o["role"]: o for o in cc.read_root(tree.parents[3])["objects"]}
        assert set(by_role) == {"chat", "link"}
        assert by_role["chat"]["shape"] == "chat_capture"
        assert by_role["link"]["shape"] == "document_capture"
        assert not any("shape_role_disagreement" in o for o in by_role.values())

    def test_a_link_path_holding_a_chat_manifest_keeps_both_facts(self, tree):
        write(tree, "people/x/oc_x/links/slug", chat_manifest())
        record = cc.read_root(tree.parents[3])["objects"][0]
        assert record["role"] == "link"
        assert record["shape"] == "chat_capture"

    def test_neither_family_is_unknown_rather_than_resolved_by_the_path(self, tree):
        write(tree, "people/x/oc_x/links/slug", {"something": "else"})
        record = cc.read_root(tree.parents[3])["objects"][0]
        assert record["shape"] == "UNKNOWN_SHAPE"
        assert record["role"] == "link"
        assert "the path says role `link`" in record["shape_role_disagreement"]
        assert "no shape this reader knows" in record["shape_role_disagreement"]

    def test_a_chat_key_without_a_sync_block_is_not_a_chat_capture(self, tree):
        """`chat` alone would be read as a capture whose coverage half vanished.

        The family is `chat`+`sync` together, because everything the declared
        half can report about a chat — `mode`, `method` — comes from `sync`. A
        manifest carrying one without the other is a shape this reader does not
        know, and calling it a chat capture would turn "no coverage record was
        written" into a page of UNDECIDABLE components.
        """
        write(tree, "people/x/oc_x", {"chat": {"chat_id": "oc_x"}, "contact": {}})
        record = cc.read_root(tree.parents[3])["objects"][0]
        assert record["shape"] == "UNKNOWN_SHAPE"
        assert "coverage" not in record
        assert record["role"] == "chat"


# --------------------------------------------------------------------------
# a page captured from outside this workspace
# --------------------------------------------------------------------------

class TestAWebPageIsItsOwnShapeAndItsOwnQuestions:
    """`docs/EXTERNAL-REFERENCE-LIBRARY.md` adds a shape, and a shape is not a
    label: the questions its completeness is asked over are not a chat's and not
    a document's, so it gets its own components and its own denials.
    """

    def test_the_plane_says_the_role_and_the_keys_say_the_shape(self, page):
        """Read from the path and from the keys, and the two agree here."""
        record = the_page(page)
        assert record["shape"] == "web_capture"
        assert record["role"] == "web"
        assert "shape_role_disagreement" not in record

    def test_a_shape_this_reader_already_knew_is_not_reclassified(self, tmp_path):
        """The page keys are asked LAST, so adding the branch moved no reading.

        A manifest that already read as a document capture reads as one still,
        even when it also carries `source` and `fetch`. A classification changed
        by an edit is the reader moving, whichever direction it moved in.
        """
        tree = tmp_path / "records" / "information" / "raw" / "feishu" / "chats"
        tree.mkdir(parents=True)
        records = tmp_path / "records"
        write_web(records, WEB_SLUG,
                  {**web_manifest(), "url": "https://example.invalid/doc",
                   "content_sha256": "a" * 64})
        assert cc.read_root(records)["objects"][0]["shape"] == "document_capture"

    def test_the_capture_half_is_what_was_asked_for(self, page):
        """`requested_url` is the request. Where it ENDED UP is the response's
        fact, kept apart because a declaration names an address to read and not
        wherever a server chose to send the reader."""
        record = the_page(page)
        assert record["capture"]["requested_url"] == \
            "https://example.invalid/article"
        assert record["capture"]["method"] == "curl"
        assert record["capture_undeclared"] == []

    def test_a_capture_that_recorded_no_method_says_so_rather_than_guessing(
            self, tmp_path):
        tree = tmp_path / "records" / "information" / "raw" / "feishu" / "chats"
        tree.mkdir(parents=True)
        records = tmp_path / "records"
        write_web(records, WEB_SLUG, web_manifest(
            fetch={"http_status": 200, "bytes": 10, "sha256": "b" * 64}))
        record = the_page(records)
        assert "method" not in record["capture"]
        assert [u["field"] for u in record["capture_undeclared"]] == ["method"]

    def test_a_body_with_a_short_digest_is_incomplete(self, tmp_path):
        """A 12-character digest names a body; it does not address one."""
        tree = tmp_path / "records" / "information" / "raw" / "feishu" / "chats"
        tree.mkdir(parents=True)
        records = tmp_path / "records"
        write_web(records, WEB_SLUG, web_manifest(
            fetch={"at": "2026-09-23T18:31+08:00", "http_status": 200,
                   "bytes": 317035, "sha256": "b" * 12, "captured_by": "curl"}))
        body = the_page(records)["coverage"]["components"]["body"]
        assert body["state"] == "INCOMPLETE"
        assert body["facts"]["content_sha256_chars"] == 12

    def test_a_page_that_was_never_inventoried_is_undecidable_and_not_empty(self):
        """§5's `0 材料且 parser 未知不得 0/0 = 100%`, at the page's own level.

        `materials.inventoried: false` with `count: 0` is one reading — nobody
        looked — and reading the zero as "this page holds nothing" would report
        a pass nobody measured. The count stays in `facts` so the number a
        person needs is not hidden; only its MEANING is refused.
        """
        components = cc._web_components(web_manifest())
        materials = components["materials"]
        assert materials["state"] == "UNDECIDABLE"
        assert materials["facts"]["materials_count"] == 0
        assert materials["facts"]["materials_inventoried"] is False
        assert "0/0" in " ".join(materials["why"])

    def test_a_page_whose_materials_were_inventoried_reports_a_measured_zero(self):
        """The other side of the same rule: a zero over a real look is a zero."""
        components = cc._web_components(
            web_manifest(materials={"inventoried": True, "count": 0}))
        materials = components["materials"]
        assert materials["state"] == "COMPLETE"
        assert materials["facts"]["materials_count"] == 0

    def test_a_missing_inventory_state_is_undecidable_and_not_a_pass(self):
        components = cc._web_components(web_manifest(materials={"count": 0}))
        assert components["materials"]["state"] == "UNDECIDABLE"

    def test_a_200_is_a_response_and_a_refusal_is_not(self):
        """A 403 often comes with a body — a page, sometimes a nice one. The
        bytes can be kept and still not be the page, so the two questions are
        two states."""
        ok = cc._web_components(web_manifest())["response"]
        assert ok["state"] == "COMPLETE"
        assert ok["facts"]["http_status"] == 200
        refused = cc._web_components(web_manifest(fetch={
            "at": "x", "http_status": 403, "bytes": 900, "sha256": "b" * 64,
            "captured_by": "curl"}))["response"]
        assert refused["state"] == "INCOMPLETE"
        assert "403" in " ".join(refused["why"])

    def test_a_missing_status_is_undecidable_and_not_a_pass(self):
        components = cc._web_components(web_manifest(
            fetch={"at": "x", "bytes": 10, "sha256": "b" * 64}))
        assert components["response"]["state"] == "UNDECIDABLE"
        assert "fetch.http_status" in " ".join(components["response"]["why"])

    def test_a_redirect_is_reported_without_calling_the_capture_failed(self):
        """The bytes are real and the address is not the one that was asked for.
        Both are true, so neither is rounded into the other: the component stays
        COMPLETE and the sentence names both addresses."""
        components = cc._web_components(web_manifest(source={
            "provider": "web", "requested_url": "https://example.invalid/a",
            "final_url": "https://example.invalid/b", "redirected": True}))
        response = components["response"]
        assert response["state"] == "COMPLETE"
        assert response["facts"]["redirected"] is True
        assert response["facts"]["final_url"] == "https://example.invalid/b"
        assert "example.invalid/b" in " ".join(response["why"])

    def test_the_identity_block_keeps_the_address_out_of_the_chat_id(self, page):
        record = the_page(page)
        assert record["identity"]["chat_id"] is None
        assert record["identity"]["name"] == "An Article"
        assert record["identity"]["url"] == "https://example.invalid/article"

    def test_a_fetch_that_never_re_checked_anything_says_not_recorded(self, page):
        """A one-shot fetch has no re-check to record, and inventing one would
        put this reader's assumption where the collector's evidence goes."""
        verified = the_page(page)["coverage"]["verified"]
        assert verified["state"] == "NOT_RECORDED"
        assert verified["at"] is None

    def test_every_web_fact_is_named_by_the_producer_table(self, page):
        """The table is load-bearing: a fact with no named producer refuses."""
        record = the_page(page)
        assert set(record["capture"]) <= set(cc.CAPTURE_PRODUCED)
        facts = set()
        for name, component in record["coverage"]["components"].items():
            assert component["facts"], f"`{name}` claims a state with no facts"
            facts |= set(component["facts"])
        assert facts <= set(cc.COVERAGE_PRODUCED)
        assert {"materials_inventoried", "materials_count", "http_status",
                "final_url", "redirected"} <= facts

    def test_an_unknown_shape_names_all_three_key_pairs(self, tree):
        write(tree, "people/x/oc_x/links/slug", {"something": "else"})
        record = cc.read_root(tree.parents[3])["objects"][0]
        assert "`source`+`fetch`" in record["why"]


# --------------------------------------------------------------------------
# producers are named, and an unnamable field is a refusal
# --------------------------------------------------------------------------

class TestEveryFieldHasAProducer:
    def test_the_two_halves_name_two_disjoint_sets_of_producers(self, tree):
        write(tree, "people/x/oc_x", chat_manifest())
        write(tree, "people/x/oc_x/links/slug", document_manifest())
        for record in cc.read_root(tree.parents[3])["objects"]:
            assert set(record["capture"]) <= set(cc.CAPTURE_PRODUCED)
            facts = set()
            for component in record["coverage"]["components"].values():
                facts |= set(component["facts"])
            assert facts <= set(cc.COVERAGE_PRODUCED)
        # The declaration half and the materialization half must not share a
        # producer: a field in both tables is a field whose half is undecided.
        assert not set(cc.CAPTURE_PRODUCED) & set(cc.COVERAGE_PRODUCED)

    def test_a_capture_key_with_no_producer_is_a_refusal_not_a_field(self, tree, monkeypatch):
        write(tree, "people/x/oc_x", chat_manifest())
        original = cc._capture_half

        def with_smuggled(shape, document):
            declared, undeclared = original(shape, document)
            declared["smuggled"] = 1
            return declared, undeclared

        monkeypatch.setattr(cc, "_capture_half", with_smuggled)
        with pytest.raises(cc.Refused) as caught:
            cc.read_root(tree.parents[3])
        assert "`capture.smuggled`" in str(caught.value)
        assert "does not name a source for" in str(caught.value)

    def test_a_reader_defect_is_not_reported_as_an_unreadable_manifest(self, tree, monkeypatch):
        """A `Refused` about this module must not become a claim about the file.

        `read_root` turns a `Malformed` into an UNREADABLE object, because that
        is a fact about the manifest. If it caught every `Refused`, a bug here
        would print as "this file cannot be read" — the misattribution the
        components exist to prevent, one level up.
        """
        write(tree, "people/x/oc_x", chat_manifest())
        monkeypatch.setattr(cc, "CAPTURE_PRODUCED", {})
        with pytest.raises(cc.Refused):
            cc.read_root(tree.parents[3])
        # And the per-file case still becomes a record rather than an abort.
        write(tree, "people/x/oc_y", {"chat": "not an object", "sync": {}})
        monkeypatch.undo()
        shapes = {o["path"]: o["shape"]
                  for o in cc.read_root(tree.parents[3])["objects"]}
        assert shapes["feishu/chats/people/x/oc_y/manifest.json"] == "UNREADABLE"

    def test_a_coverage_key_with_no_producer_is_a_refusal(self):
        with pytest.raises(cc.Refused) as caught:
            cc._facts(("invented", 1))
        assert "`coverage.invented`" in str(caught.value)

    def test_every_component_state_is_in_the_declared_vocabulary(self, tree):
        write(tree, "people/x/oc_x", chat_manifest())
        write(tree, "people/x/oc_x/links/slug", document_manifest())
        for record in cc.read_root(tree.parents[3])["objects"]:
            for component in record["coverage"]["components"].values():
                assert component["state"] in cc.COMPONENT_STATES


# --------------------------------------------------------------------------
# the reader reads no clock
# --------------------------------------------------------------------------

class TestTheReaderHoldsNoClock:
    def test_no_time_module_is_imported(self):
        source = (REPO_ROOT / "intake" / "capture_coverage.py").read_text(encoding="utf-8")
        assert "import time" not in source
        assert "import datetime" not in source
        assert "time.time" not in source
        assert "datetime.now" not in source

    def test_the_verification_stamp_is_printed_as_written_and_not_aged(self, tree):
        document = chat_manifest()
        document["sync"]["last_verified_at"] = "2020-01-01T00:00:00"
        write(tree, "people/x/oc_x", document)
        verified = cc.read_root(tree.parents[3])["objects"][0]["coverage"]["verified"]
        assert verified["state"] == "RECORDED"
        assert verified["at"] == "2020-01-01T00:00:00"

    def test_an_unverified_manifest_says_so_rather_than_going_quiet(self, tree):
        document = chat_manifest()
        del document["sync"]["last_verified_at"]
        write(tree, "people/x/oc_x", document)
        verified = cc.read_root(tree.parents[3])["objects"][0]["coverage"]["verified"]
        assert verified["state"] == "NOT_RECORDED"
        assert verified["at"] is None
        assert "the collector's own account of its own work" in verified["why"]


# --------------------------------------------------------------------------
# the verb
# --------------------------------------------------------------------------

class TestTheCli:
    def test_it_renders_the_live_tree_and_exits_zero(self):
        done = cli("information", "captured")
        assert done.returncode == 0, done.stderr
        assert "按分量计" in done.stdout
        assert "一份材料失败不伪造整包完成" not in done.stdout  # no such claim is made

    def test_a_relative_records_root_is_refused(self):
        done = cli("information", "captured", "--records-root", "records")
        assert done.returncode == 1
        assert "must be absolute" in done.stderr
        assert "nobody else can reproduce" in done.stderr

    def test_an_absent_records_root_refuses_with_a_reason(self, tmp_path):
        done = cli("information", "captured", "--records-root", str(tmp_path))
        assert done.returncode == 1
        assert "not a directory" in done.stderr
        assert "not an empty one" in done.stderr

    def test_json_is_the_same_report_the_renderer_prints(self, tmp_path, tree):
        write(tree, "people/x/oc_x", chat_manifest())
        records = tree.parents[3]
        done = cli("information", "captured", "--records-root", str(records), "--json")
        assert done.returncode == 0, done.stderr
        payload = json.loads(done.stdout)
        assert payload["schema"] == cc.SCHEMA
        assert payload["summary"]["denominator"] == 1
        assert payload["summary"] == cc.read_root(records)["summary"]

    def test_it_loads_the_module_rather_than_shelling_out_to_it(self):
        source = (REPO_ROOT / "runtime" / "cli.py").read_text(encoding="utf-8")
        body = source.split("def cmd_information_captured(")[1].split("\ndef ")[0]
        assert "intake" in body and "capture_coverage.py" in body
        assert "subprocess" not in body
        # And the module has no `main()` for a verb to reach for instead.
        assert not hasattr(cc, "main")


# --------------------------------------------------------------------------
# the capture
# --------------------------------------------------------------------------

def _verified_on(entry: dict) -> str:
    """The day the collector says it verified this manifest, or `""`.

    `capture_coverage` copies the collector's own `verified.at` rather than
    reading a clock, so this is the tree's date and not this run's. Both
    spellings are in the tree — a bare day and a full timestamp — and only the
    day is compared, which is the resolution the capture's own name has.
    """
    verified = (entry.get("coverage") or {}).get("verified") or {}
    return str(verified.get("at") or "")[:10]


def _trimmed(entry: dict) -> dict:
    """One live object reduced the way the capture file was reduced.

    The trim the capture documents, recovered by measurement rather than read
    off a script: `coverage.components` flattened to `components` with the
    `why` sentences dropped, `coverage.verified.state` to `verified`, and each
    `capture_undeclared` entry to the field name it names. Applied to the live
    tree it reproduces 65 of the 66 captured objects exactly; the 66th is one
    the collector rewrote after the capture, and the test below separates that
    from a reader that moved rather than counting it as either.
    """
    coverage = entry.get("coverage") or {}
    components = {name: {"state": c.get("state"), "facts": c.get("facts")}
                  for name, c in (coverage.get("components") or {}).items()}
    return {
        "path": entry["path"],
        "role": entry["role"],
        "shape": entry["shape"],
        "capture": entry["capture"],
        "capture_undeclared": [u["field"] for u in entry["capture_undeclared"]],
        "components": components,
        "verified": (coverage.get("verified") or {}).get("state"),
    }


CAPTURED_PATH = "feishu/chats/people/x/oc_x/manifest.json"


def _captured_entry(msg_count: int) -> dict:
    """One object as the capture file holds it — trimmed, `why` dropped."""
    return {
        "path": CAPTURED_PATH,
        "role": "chat",
        "shape": "chat_capture",
        "capture": {"mode": "incremental"},
        "capture_undeclared": ["mode"],
        "components": {"messages": {"state": "COMPLETE",
                                    "facts": {"msg_count": msg_count}}},
        "verified": "RECORDED",
    }


def _live_entry(msg_count: int, verified_at: str) -> dict:
    """The same object as the reader returns it — nested, with prose."""
    return {
        "path": CAPTURED_PATH,
        "role": "chat",
        "shape": "chat_capture",
        "capture": {"mode": "incremental"},
        "capture_undeclared": [{"field": "mode", "produced_by": "sync.mode",
                                "why": "the collector did not record this"}],
        "coverage": {
            "components": {"messages": {"state": "COMPLETE",
                                        "facts": {"msg_count": msg_count},
                                        "why": []}},
            "verified": {"state": "RECORDED", "at": verified_at, "why": ""},
            "unstructured": {},
        },
        "identity": {"chat_id": "oc_x", "name": "X"},
        "prev_content_sha256": None,
    }


def _disagreements(recorded: dict, live: dict) -> tuple[list, list, list]:
    """Sort the ways a re-run can differ into the three that are not the same.

    `gone` is material the capture read and this run does not. `moved` is a
    reading that changed while the manifest's own record does NOT say the
    collector re-wrote it after the capture — that is the reader having moved,
    and it is the failure. `arrived` is material the capture could not have
    seen. A reading that changed AND was re-verified is in none of the three:
    it is the tree advancing, which is not a finding about this module.

    Separated from the live-tree test below because the live tree can only show
    whichever case it happens to be in, and today it shows only an advance. The
    made-up manifests in `TestWhatCountsAsATreeThatMoved` are where the
    discriminator is actually asked.
    """
    gone = sorted(set(recorded) - set(live))
    # Only the paths BOTH sides hold can be compared: a path the capture read
    # and this run does not is `gone`, and asking it for a reading is asking a
    # dictionary for a key nobody put there.
    both = sorted(set(recorded) & set(live))
    changed = [p for p in both if _trimmed(live[p]) != recorded[p]]
    moved = [p for p in changed if _verified_on(live[p]) <= CAPTURED_ON]
    arrived = sorted(set(live) - set(recorded))
    return gone, moved, arrived


class TestWhatCountsAsATreeThatMoved:
    """The three ways a re-run can differ, asked of manifests a test makes up.

    The live tree is one sample and today it is an advancing one, so a
    discriminator tested only against it would be a discriminator that has
    never been asked a hard question. These are the hard ones.
    """

    def test_the_trim_pairs_the_captures_shape_with_the_readers(self):
        """The comparison is only a comparison if both sides are the same object.

        The capture file is the reader's output with the prose dropped and the
        coverage nested one level shallower. If the trim did not invert exactly
        that, every object would differ and the live-tree test would pass by
        asserting `moved` over a set of 66 — that is, it would be red for a
        reason that is not a finding.
        """
        assert _trimmed(_live_entry(38, "2026-09-20T10:00:00")) == \
            _captured_entry(38)

    def test_an_unchanged_manifest_is_in_no_category(self):
        recorded = {CAPTURED_PATH: _captured_entry(38)}
        live = {CAPTURED_PATH: _live_entry(38, "2026-09-14T09:00:00")}
        assert _disagreements(recorded, live) == ([], [], [])

    def test_a_manifest_the_collector_rewrote_is_neither_the_reader_nor_a_finding(self):
        """The reader said 38 and now says 92 — because the TREE says it changed."""
        recorded = {CAPTURED_PATH: _captured_entry(38)}
        live = {CAPTURED_PATH: _live_entry(92, "2026-09-23T12:47:12")}
        gone, moved, arrived = _disagreements(recorded, live)
        assert (gone, moved, arrived) == ([], [], [])
        # Not vacuous: the reading really did differ, and the trim shows it.
        assert _trimmed(live[CAPTURED_PATH]) != recorded[CAPTURED_PATH]

    def test_a_reading_that_changed_without_a_reverification_is_the_reader_moving(self):
        recorded = {CAPTURED_PATH: _captured_entry(38)}
        live = {CAPTURED_PATH: _live_entry(92, "2026-09-14T09:00:00")}
        assert _disagreements(recorded, live) == ([], [CAPTURED_PATH], [])

    def test_the_day_of_the_capture_does_not_count_as_after_it(self):
        """`<=` and not `<`: a manifest verified ON the capture day is not evidence
        of anything the capture could not have seen, so it stays a finding."""
        recorded = {CAPTURED_PATH: _captured_entry(38)}
        live = {CAPTURED_PATH: _live_entry(92, CAPTURED_ON + "T23:59:59")}
        assert _disagreements(recorded, live) == ([], [CAPTURED_PATH], [])

    def test_a_missing_verified_date_is_a_finding_and_not_a_pass(self):
        """A manifest whose own record cannot say when it was verified is exactly
        the case where this module must not assume the tree moved."""
        recorded = {CAPTURED_PATH: _captured_entry(38)}
        live = {CAPTURED_PATH: _live_entry(92, "")}
        assert _disagreements(recorded, live) == ([], [CAPTURED_PATH], [])

    def test_a_manifest_the_capture_read_and_this_run_does_not_is_gone(self):
        recorded = {CAPTURED_PATH: _captured_entry(38)}
        assert _disagreements(recorded, {}) == ([CAPTURED_PATH], [], [])

    def test_a_manifest_the_capture_could_not_have_seen_has_arrived(self):
        live = {CAPTURED_PATH: _live_entry(38, "2026-09-23T12:47:12")}
        assert _disagreements({}, live) == ([], [], [CAPTURED_PATH])


@pytest.mark.skipif(not FIXTURE.exists(), reason="no capture recorded")
class TestAgainstTheRecordedCapture:
    """What the 2026-09-22 capture reads, and what a re-run must still say."""

    def test_the_recorded_totals(self):
        """The captured numbers, as the shipped file's own record.

        These pin the FILE, not the reader's current output: the capture is
        what was read on 2026-09-22, and a total here changing means the
        shipped artifact was edited rather than that the reader moved. Whether
        the reader still produces these numbers of the manifests is a different
        question and is asked per manifest, below.
        """
        captured = json.loads(FIXTURE.read_text(encoding="utf-8"))["summary"]
        assert captured["denominator"] == 66
        assert captured["by_shape"] == {"chat_capture": 47, "document_capture": 19}
        assert captured["by_role"] == {"chat": 47, "link": 19}
        assert captured["components"]["messages"] == {"COMPLETE": 45,
                                                     "UNDECIDABLE": 2}
        assert captured["components"]["body"] == {"COMPLETE": 16, "INCOMPLETE": 3}
        assert captured["components"]["media"] == {"COMPLETE": 14, "UNDECIDABLE": 5}
        assert captured["capture_undeclared"] == {"mode": 37}
        assert captured["verified"] == {"RECORDED": 61, "NOT_RECORDED": 5}

    def test_the_live_tree_still_produces_what_the_capture_recorded(self):
        """Each captured manifest reads the same, unless the tree says it changed.

        Per manifest rather than by total, because the tree is alive: since
        2026-09-22 it gained one capture and the collector rewrote one manifest
        it had already written. A total comparison makes both of those look
        like the reader having moved, which is the scalar mistake this module's
        neighbour exists to stop — a total standing in for the positions under
        it. Three things are asked separately instead:

          * nothing the capture read has gone;
          * nothing reads differently WITHOUT the manifest's own record saying
            it was re-verified after the capture — `coverage.verified.at` is
            the collector's date, so a changed reading whose manifest was not
            re-verified is the READER moving, and that is the failure;
          * the denominator grew by exactly the manifests that are new, so a
            shrink cannot hide behind an arrival.
        """
        if not LIVE_RECORDS.is_dir():
            pytest.skip("this node's records checkout is not here")
        report = cc.read_root(LIVE_RECORDS)
        captured = json.loads(FIXTURE.read_text(encoding="utf-8"))
        recorded = {o["path"]: o for o in captured["objects"]}
        live = {o["path"]: o for o in report["objects"]}

        gone, moved, arrived = _disagreements(recorded, live)
        assert gone == [], (
            f"the capture read these and this run does not: {gone}. A manifest "
            f"that disappeared is a finding about the tree, not a re-reading")
        assert moved == [], (
            f"these read differently and their own record does not say the "
            f"collector rewrote them after the capture ({CAPTURED_ON}), so what "
            f"moved is the reader, not the tree: {moved}")

        assert report["summary"]["denominator"] == len(recorded) + len(arrived)
        assert (report["summary"]["denominator"]
                >= captured["summary"]["denominator"]), (
            "the tree cannot hold fewer manifests than the capture read from it")

    def test_the_denominator_equals_the_files_that_are_on_disk(self):
        if not LIVE_RECORDS.is_dir():
            pytest.skip("this node's records checkout is not here")
        root = cc.raw_root(LIVE_RECORDS)
        on_disk = {str(p.relative_to(root)) for p in cc.walk_manifests(root)}
        report = cc.read_root(LIVE_RECORDS)
        assert len(on_disk) == report["summary"]["denominator"]
        assert {o["path"] for o in report["objects"]} == on_disk

    def test_the_three_truncated_digests_are_the_finding_and_not_a_slip(self):
        """A 12-character `content_sha256` names a body without addressing one."""
        if not LIVE_RECORDS.is_dir():
            pytest.skip("this node's records checkout is not here")
        report = cc.read_root(LIVE_RECORDS)
        short = [o for o in report["objects"]
                 if (o.get("coverage") or {}).get("components", {}).get("body", {})
                 .get("facts", {}).get("content_sha256_chars", 64) < 64]
        assert len(short) == 3
        for record in short:
            assert record["coverage"]["components"]["body"]["state"] == "INCOMPLETE"
            assert record["coverage"]["components"]["body"]["facts"][
                "content_sha256_chars"] == 12

    def test_the_two_unpaginated_chats_carry_no_independent_denominator(self):
        if not LIVE_RECORDS.is_dir():
            pytest.skip("this node's records checkout is not here")
        report = cc.read_root(LIVE_RECORDS)
        undecided = [o for o in report["objects"]
                     if (o.get("coverage") or {}).get("components", {})
                     .get("messages", {}).get("state") == "UNDECIDABLE"]
        assert sorted(o["path"] for o in undecided) == sorted([
            "feishu/chats/people/venus-liu/oc_89c0764c0664c7ec492220164e29a328/manifest.json",
            "feishu/chats/people/yale-huang/oc_f4b9ed4885415a496e8f0b5d24d321c8/manifest.json",
        ])
        for record in undecided:
            facts = record["coverage"]["components"]["messages"]["facts"]
            assert "pagination_complete" not in facts
            assert "remote_unique_message_count" not in facts
