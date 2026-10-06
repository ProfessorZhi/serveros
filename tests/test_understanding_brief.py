"""§B2/P3 — the packet a native Agent reads, and the authority it commits under.

WHY THIS FILE EXISTS
--------------------
`understanding_semantic` decides whether a reading was made against the version
the source is at now, and it can decide that only because it never fetched
anything: it is handed a reading. The other end of that was missing — something
that hands a native Agent the material at a FIXED version and says which two
digests the reading must carry back — and so was the other half of the same
sentence: a reading committed under authority the submitter asserted.

Two axes, and the assertions are about what each one would have to be wrong for
the other to look fine:

  1. THE PACKET IS THE PIN. `source` and `read` are emitted exactly as the
     validator compares them, so an Agent session that copies those two blocks
     out of the packet commits and one that reconstructs them does not. The
     tests here take a real brief, build a reading out of nothing but its own
     two blocks, and commit it through the verb; and they check the same path
     with one field of the pin altered, which must be refused.

  2. THE AUTHORITY IS RESOLVED, NOT ASSERTED. `--grant` names a grant REF and
     the scope, window and root come from the record in Agent Control. A ref
     nothing recorded, a revoked grant and a grant whose window has passed are
     three different refusals, and a grant that records no `covers` covers
     nothing rather than covering everything — the safe direction, asserted as
     "the claim landed as a candidate" rather than as an error, because it is
     not one.

WHAT IS ASSERTED HERE, AND WHAT IS NOT
--------------------------------------
Not that a model can be trusted to write a good reading: no model runs in this
file, and the readings below are written by hand out of the packet's own
blocks, which is exactly what the packet is for. What is asserted is that the
material, the pin and the authority are the three the record says — and that a
source which moves in between makes the old reading refused and the old claim
not recallable, which is the whole point of pinning the version in the first
place.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "runtime" / "cli.py"
STORE_MODULE = REPO_ROOT / "runtime" / "agent_control_store.py"


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(
        "serveros_intake_" + name, REPO_ROOT / "intake" / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules["serveros_intake_" + name] = module
    spec.loader.exec_module(module)
    return module


#: Loaded under the names `understanding_brief._sibling` uses, so the module
#: objects are the same ones the brief loaded: two copies of
#: `understanding_semantic` would be two `Refused` classes, and a test written
#: against one would not catch the other's refusal.
brief = _load("understanding_brief", "understanding_brief.py")
us = _load("understanding_semantic", "understanding_semantic.py")
delta = _load("understanding_delta", "understanding_delta.py")
capture = _load("capture_coverage", "capture_coverage.py")


def _load_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


store_mod = _load_path("serveros_runtime_agent_control_store", STORE_MODULE)

CARD = "information/understanding/people/x.md"
VIEW = "information/understanding/projects/widget.md"
RAW = "information/raw/feishu/chats/people/x/oc_x"
MANIFEST_KEY = "feishu/chats/people/x/oc_x/manifest.json"
SOURCE = f"{RAW}/messages.jsonl"
STAMP = "2026-09-23T10:00:00+08:00"
NOW = "2026-09-23T02:00:00+00:00"


#: The card cites a SHORTER reach than the capture holds, which is the only
#: shape that produces a review range at all: a card whose citation already
#: reaches the newest position is CURRENT and the reader proposes nothing. The
#: capture holds 10; the card has understood 0–4; the range is 5–9.
CITED = "0-4"
CAPTURED = 10
FIRST_UNREAD = 5
LAST = 9


def card_prose(source: str = SOURCE, span: str = CITED) -> str:
    return ("# X\n\n手写。\n\n## Sources\n\n"
            f"- `{source}` message_position {span}\n")


VIEW_PROSE = "# widget\n\n项目视角。\n"


def records_tree(tmp_path: Path, *, prose: str | None = None,
                 count: int = CAPTURED, flags: dict | None = None) -> Path:
    """A records checkout with a capture, a card behind it, and a view.

    Built here rather than imported from the semantic suite: this file has to
    fail if the reader's shape moves, and a fixture shared between two layers
    would let both drift together and stay green.
    """
    root = tmp_path / "records"
    for relative in ("information/understanding/people",
                     "information/understanding/projects", RAW):
        (root / relative).mkdir(parents=True)
    (root / RAW / "manifest.json").write_text(json.dumps({
        "contact": {"name": "X"},
        "chat": {"chat_id": "oc_x", "chat_type": "p2p", "name": "X"},
        "sync": {"mode": "incremental", "method": "im/chat-messages-list",
                 "msg_count": count, "remote_unique_message_count": count,
                 "fetched_page_count": 1, "pagination_complete": True,
                 "earliest_create_time": "2026-09-11 09:45",
                 "latest_create_time": "2026-09-11 10:07",
                 "last_message_id": f"om_{count - 1}"},
    }, ensure_ascii=False), encoding="utf-8")
    lines = []
    for position in range(count):
        record = {"message_position": str(position), "message_id": f"om_{position}",
                  "content": f"第{position}条", "updated": False, "deleted": False}
        record.update((flags or {}).get(position, {}))
        lines.append(json.dumps(record, ensure_ascii=False))
    (root / RAW / "messages.jsonl").write_text("\n".join(lines) + "\n",
                                               encoding="utf-8")
    (root / CARD).write_text(card_prose() if prose is None else prose,
                             encoding="utf-8")
    (root / VIEW).write_text(VIEW_PROSE, encoding="utf-8")
    return root


def edit_in_place(records: Path, position: int = 6,
                  content: str = "第6条（改过）") -> None:
    """Change a record where it stands, as the collector records an edit.

    A change INSIDE the span a claim cites, which is the only change that makes
    that claim stop being current. Appending past the end does not: a claim
    about positions 5–9 is not affected by material arriving at 10, and a test
    that asserted staleness after an append would be asserting the opposite of
    the rule.
    """
    messages = records / RAW / "messages.jsonl"
    lines = messages.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[position])
    record["content"] = content
    record["updated"] = True
    lines[position] = json.dumps(record, ensure_ascii=False)
    messages.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_record(records: Path, position: int = 10,
                  content: str = "后来又说了别的") -> None:
    """Material arriving past the end: the span on offer grows to include it."""
    with (records / RAW / "messages.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps({"message_position": str(position),
                              "message_id": f"om_{position}", "content": content,
                              "updated": False, "deleted": False},
                             ensure_ascii=False) + "\n")


def _report_ranges(card_row: dict) -> list[dict]:
    return list(card_row["judged"]["review_ranges"])


def report_for(records: Path) -> dict:
    return delta.read_and_reconcile(records, capture.read_root(records)["objects"])


def card_and_range(records: Path, card: str = CARD) -> tuple[dict, dict]:
    """The card row and the one review range the reader proposes for it.

    Two values rather than one because `brief_for` needs both and they look
    alike: the card carries the `card_digest` a reading is pinned to and the
    range carries the `content_identity`, and a caller that conflated them would
    be pinning a reading to a digest of the wrong file.
    """
    report = report_for(records)
    card_row = next(item for item in report["cards"] if item["card"] == card)
    return card_row, next(iter(card_row["judged"]["review_ranges"]))


def briefs_for(records: Path) -> list[dict]:
    return brief.briefs(report_for(records), records)[0]


def unpinnable_for(records: Path) -> list[dict]:
    return brief.briefs(report_for(records), records)[1]


def the_brief(records: Path, card: str = CARD) -> dict:
    return next(item for item in briefs_for(records)
                if item["source"]["card"] == card)


def reading_from(packet: dict, claims=None) -> dict:
    """A reading built from nothing but the packet's own two blocks.

    That is the claim the packet makes about itself: everything the validator
    compares is in front of the Agent, copied. Nothing here consults the records
    tree, the reader or the clock.
    """
    return {
        "schema": packet["reading"]["schema"],
        "source": dict(packet["source"]),
        "read": dict(packet["read"]),
        "claims": claims if claims is not None else [{
            "id": "c1", "kind": us.OBSERVED_FACT, "subject": "x",
            "assertion": {"was": None, "now": "她说周五交付"},
            "support": {"positions": [packet["source"]["to_position"]],
                        "excerpt": "周五给你"},
            "applies_to": None, "touches": [], "supersedes": [], "conflicts": [],
            "card": packet["source"]["card"], "views": [VIEW],
        }],
    }


def delegation_file(tmp_path: Path, *, covers=("x",)) -> Path:
    """`understanding_semantic`'s own contract: the standing grant as a FILE.

    Used for the tests that are about the PIN, so their authority is the plain
    one and a failure cannot be a failure of the store join.
    """
    path = tmp_path / "delegation.json"
    path.write_text(json.dumps({"covers": list(covers), "until": None,
                                "granted_by": "user", "source": "session-1"}),
                    encoding="utf-8")
    return path


def cli(*args, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(CLI), *map(str, args)],
                          capture_output=True, text=True, cwd=str(REPO_ROOT),
                          env=env)


def data_root(tmp_path: Path) -> dict:
    """A fixture DATA_ROOT, so `paths` resolves the control store inside the test."""
    data = tmp_path / "data"
    for area in ("state/serveros/runtime/agent-control", "state/boards",
                 "workspace/records", "artifacts/work-items"):
        (data / area).mkdir(parents=True, exist_ok=True)
    return {"DATA_ROOT": str(data), "HOME": str(data),
            "WORKSPACE_ROOT": str(data / "workspace"),
            "ARTIFACTS_ROOT": str(data / "artifacts"),
            "SERVEROS_STATE_DIR": str(data / "state" / "serveros")}


#: 2026-09-23T01:58:00Z observed, FRESH until +300s — the same freshness numbers
#: `context/actor-context-contract.yaml` publishes and `tests/test_delegation.py`
#: asserts. `NOW` is inside that window and inside the grant's, so a refusal
#: below is about the thing the test names and not about a window.
CONTEXT_REF = "human-context:botmux-human-control:" + "a" * 32
OBSERVED_AT = "2026-09-23T01:58:00+00:00"


def a_context(**over) -> dict:
    record = {"context_ref": CONTEXT_REF, "actor_ref": "human:hwz",
              "provenance": "FACT", "provider_status": "VERIFIED",
              "observed_at": OBSERVED_AT,
              "expires_at": "2026-09-23T02:13:00+00:00"}
    record.update(over)
    return record


def a_grant(**over) -> dict:
    record = {"grant_ref": "grant:x", "delegated_by": CONTEXT_REF,
              "valid_from": "2026-09-22T00:00:00+00:00",
              "expires_at": "2026-10-01T00:00:00+00:00",
              "covers": ["x"],
              "budget": {"wall_seconds": 600, "max_attempts": 2,
                         "max_output_bytes": 65536, "exhausted": False}}
    record.update(over)
    return record


def seeded(tmp_path: Path, *, grant=None, context=None) -> dict:
    """A DATA_ROOT env whose control store holds one context and one grant."""
    env = data_root(tmp_path)
    store = store_mod.AgentControlStore(
        Path(env["SERVEROS_STATE_DIR"]) / "runtime" / "agent-control" / "store.json")
    store.put_context(context or a_context())
    if grant is not False:
        store.put_grant(grant or a_grant())
    return env


def write_reading(tmp_path: Path, document: dict, name: str = "reading.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# 1. the packet is the pin
# --------------------------------------------------------------------------

class TestThePacketIsThePin:

    def test_the_two_blocks_the_validator_compares_are_in_the_packet(self, tmp_path):
        records = records_tree(tmp_path)
        _, row = card_and_range(records)
        packet = the_brief(records)
        assert packet["source"] == {
            "card": row["card"], "manifest": row["manifest"],
            "from_position": row["from_position"],
            "to_position": row["to_position"]}
        assert packet["read"]["content_identity"] == row["content_identity"]
        assert packet["read"]["card_digest"] == us.card_digest(
            (records / CARD).read_text(encoding="utf-8"))

    def test_a_reading_built_from_the_packet_alone_commits(self, tmp_path):
        records = records_tree(tmp_path)
        packet = the_brief(records)
        result = cli("work", "understanding-semantic", "--records-root", records,
                     "--reading", write_reading(tmp_path, reading_from(packet)),
                     "--delegation", delegation_file(tmp_path),
                     "--commit", "--committed-at", STAMP, "--json")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["state"] == "COMPLETE"
        body = (records / CARD).read_text(encoding="utf-8")
        assert "她说周五交付" in body

    def test_a_reading_whose_pin_is_altered_is_refused(self, tmp_path):
        records = records_tree(tmp_path)
        packet = the_brief(records)
        document = reading_from(packet)
        document["read"]["content_identity"] = "0" * 64
        result = cli("work", "understanding-semantic", "--records-root", records,
                     "--reading", write_reading(tmp_path, document), "--commit",
                     "--committed-at", STAMP)
        assert result.returncode != 0
        assert "no longer the one on disk" in result.stderr
        assert (records / CARD).read_text(encoding="utf-8") == card_prose()

    def test_a_record_changed_inside_the_span_makes_the_old_reading_refused(
            self, tmp_path):
        """The pin is only worth anything if a moved source rejects it — and
        the span is the SAME span, so what refuses this is the content identity
        and nothing else."""
        records = records_tree(tmp_path)
        before = card_and_range(records)[1]
        document = reading_from(the_brief(records))
        edit_in_place(records)
        after = card_and_range(records)[1]
        assert (after["from_position"], after["to_position"]) == (
            before["from_position"], before["to_position"])
        assert after["content_identity"] != before["content_identity"]
        result = cli("work", "understanding-semantic", "--records-root", records,
                     "--reading", write_reading(tmp_path, document), "--commit",
                     "--committed-at", STAMP)
        assert result.returncode != 0
        assert "no longer the one on disk" in result.stderr
        assert (records / CARD).read_text(encoding="utf-8") == card_prose()

    def test_material_arriving_past_the_end_grows_the_span_and_refuses_the_pin(
            self, tmp_path):
        """The other way a source moves, and it is a different shape: the range
        the reader now proposes reaches further, so the old reading pins a span
        that is no longer the one on offer."""
        records = records_tree(tmp_path)
        document = reading_from(the_brief(records))
        append_record(records)
        assert card_and_range(records)[1]["to_position"] == 10
        result = cli("work", "understanding-semantic", "--records-root", records,
                     "--reading", write_reading(tmp_path, document), "--commit",
                     "--committed-at", STAMP)
        assert result.returncode != 0
        assert (records / CARD).read_text(encoding="utf-8") == card_prose()


# --------------------------------------------------------------------------
# 2. what a material row carries, and what it does not
# --------------------------------------------------------------------------

class TestTheMaterial:

    def test_the_material_is_the_records_the_reader_named_in_position_order(
            self, tmp_path):
        records = records_tree(tmp_path)
        packet = the_brief(records)
        positions = [record["position"] for record in packet["material"]["records"]]
        assert positions == sorted(positions)
        assert positions == list(range(packet["source"]["from_position"],
                                       packet["source"]["to_position"] + 1))
        assert [record["content"] for record in packet["material"]["records"]] == [
            f"第{position}条" for position in positions]

    def test_a_row_carries_four_fields_and_no_others(self, tmp_path):
        packet = the_brief(records_tree(tmp_path))
        for record in packet["material"]["records"]:
            assert sorted(record) == sorted(brief.RECORD_PRODUCED)
        assert packet["material"]["path"].endswith("messages.jsonl")

    def test_an_edited_and_a_deleted_record_are_marked_and_counted(self, tmp_path):
        """Both flags are one fact each, and a deleted record keeps its text.

        A withdrawal is exactly what a reading most needs to see; a packet that
        dropped it would hand the Agent a source that looked like it had never
        said the thing.
        """
        records = records_tree(tmp_path, flags={6: {"updated": True},
                                                7: {"deleted": True}})
        packet = the_brief(records)
        by_position = {record["position"]: record
                       for record in packet["material"]["records"]}
        assert by_position[6]["updated"] is True
        assert by_position[6]["deleted"] is False
        assert by_position[7]["deleted"] is True
        assert by_position[7]["content"] == "第7条"
        assert packet["range"]["revisions"] == {
            "edited": 1, "deleted": 1,
            "records": len(packet["material"]["records"])}
        assert packet["material"]["state"] == packet["range"]["revisions"]

    def test_the_older_spelling_of_an_edit_is_the_same_fact(self, tmp_path):
        records = records_tree(tmp_path, flags={6: {"edited": True}})
        packet = the_brief(records)
        by_position = {record["position"]: record
                       for record in packet["material"]["records"]}
        assert by_position[6]["updated"] is True
        assert packet["range"]["revisions"]["edited"] == 1

    def test_a_record_with_no_content_is_null_and_not_an_empty_message(
            self, tmp_path):
        records = records_tree(tmp_path)
        messages = records / RAW / "messages.jsonl"
        lines = messages.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[6])
        del record["content"]
        lines[6] = json.dumps(record, ensure_ascii=False)
        messages.write_text("\n".join(lines) + "\n", encoding="utf-8")
        packet = the_brief(records)
        by_position = {item["position"]: item
                       for item in packet["material"]["records"]}
        assert by_position[6]["content"] is None

    def test_a_span_with_nothing_in_it_is_refused_rather_than_handed_over(
            self, tmp_path):
        """A packet that asks a model to read nothing and calls the answer a
        reading is not a packet."""
        records = records_tree(tmp_path)
        messages = delta.messages_file(MANIFEST_KEY, records)
        records_out, why = brief.span_records(messages, 90, 99)
        assert records_out is None
        assert "90–99" in why

    def test_a_messages_file_that_is_not_there_is_refused_with_its_path(
            self, tmp_path):
        records = records_tree(tmp_path)
        missing = delta.messages_file(MANIFEST_KEY, records).with_suffix(".absent")
        records_out, why = brief.span_records(missing, 0, 9)
        assert records_out is None
        assert ".absent" in why

    def test_a_line_that_is_not_json_is_refused_by_its_line_number(self, tmp_path):
        records = records_tree(tmp_path)
        messages = delta.messages_file(MANIFEST_KEY, records)
        messages.write_text(messages.read_text(encoding="utf-8") + "{not json\n",
                            encoding="utf-8")
        records_out, why = brief.span_records(messages, 0, 20)
        assert records_out is None
        assert "line 11" in why


# --------------------------------------------------------------------------
# 3. the card half
# --------------------------------------------------------------------------

class TestTheCardHalf:

    def test_a_card_with_no_fence_says_so(self, tmp_path):
        packet = the_brief(records_tree(tmp_path))
        assert packet["card"]["claims"] is None
        assert packet["card"]["claims_state"] == "NO_FENCE"
        assert "手写。" in packet["card"]["legacy_context"]

    def test_a_fence_the_module_marked_is_read_as_marked(self, tmp_path):
        records = records_tree(tmp_path)
        (records / CARD).write_text(
            us.fence_parts(card_prose())[0]
            + "<!-- serveros:semantic:begin -->\n"
            + "<!-- serveros:semantic:claims -->\n"
            + "- c1 · OBSERVED_FACT · 她说周五交付\n"
            + "<!-- serveros:semantic:claims:end -->\n"
            + "<!-- serveros:semantic:end -->\n", encoding="utf-8")
        packet = the_brief(records)
        assert packet["card"]["claims_state"] == "MARKED"
        assert "c1" in packet["card"]["claims"]
        assert "手写。" in packet["card"]["legacy_context"]
        assert "c1" not in packet["card"]["legacy_context"]

    def test_a_fence_written_before_the_module_marked_its_lines_is_legacy(
            self, tmp_path):
        records = records_tree(tmp_path)
        (records / CARD).write_text(
            us.fence_parts(card_prose())[0]
            + "<!-- serveros:semantic:begin -->\n"
            + "- c1 · OBSERVED_FACT · 她说周五交付\n"
            + "<!-- serveros:semantic:end -->\n", encoding="utf-8")
        packet = the_brief(records)
        assert packet["card"]["claims_state"] == "UNMARKED_LEGACY"

    def test_the_digest_is_the_readers_and_is_not_taken_again(self, tmp_path):
        """A brief that took its own digest would be a second reader of one
        file, and the one that drifted would be the one the Agent was told to
        hand back."""
        records = records_tree(tmp_path)
        card_row, row = card_and_range(records)
        invented = "f" * 64
        packet = brief.brief_for({**card_row, "card_digest": invented}, row, records)
        assert packet["read"]["card_digest"] == invented
        assert packet["card"]["digest"] == invented

    def test_a_card_that_cannot_be_read_is_refused(self, tmp_path):
        records = records_tree(tmp_path)
        card_row, row = card_and_range(records)
        (records / CARD).unlink()
        with pytest.raises(brief.Refused) as refusal:
            brief.brief_for(card_row, row, records)
        assert CARD in str(refusal.value)

    def test_a_range_row_where_a_card_belongs_is_a_refusal(self, tmp_path):
        """The two halves look alike and come from one report; a module whose
        contract is refusals should not traceback on the caller's field."""
        records = records_tree(tmp_path)
        _, row = card_and_range(records)
        with pytest.raises(brief.Malformed) as refusal:
            brief.brief_for(row, row, records)
        assert "not a card row" in str(refusal.value)


# --------------------------------------------------------------------------
# 4. the shapes this packet cannot pin, refused by name
# --------------------------------------------------------------------------

class TestWhatCannotBePinned:

    def test_a_document_range_is_refused_by_name(self, tmp_path):
        """Its material is a body, and no capture-manifest field is blessed by
        this tree's producer tables as the body's locator."""
        records = records_tree(tmp_path)
        card_row, row = card_and_range(records)
        document = {**row, "from_position": None, "to_position": None,
                    "kind": "WHOLE_DOCUMENT"}
        with pytest.raises(brief.Refused) as refusal:
            brief.brief_for(card_row, document, records)
        assert "document body" in str(refusal.value)
        assert "WHOLE_DOCUMENT" in str(refusal.value)


    def test_a_range_with_no_content_identity_carries_the_readers_reason(
            self, tmp_path):
        records = records_tree(tmp_path)
        card_row, row = card_and_range(records)
        row = {**row, "content_identity": None,
               "content_identity_why": "the capture does not identify its content"}
        with pytest.raises(brief.Refused) as refusal:
            brief.brief_for(card_row, row, records)
        assert "the capture does not identify its content" in str(refusal.value)

    def test_a_field_with_no_producer_is_a_refusal(self, tmp_path):
        with pytest.raises(brief.Refused) as refusal:
            brief.no_unsourced({"position": 1, "vibe": "good"},
                               brief.RECORD_PRODUCED, "a material row")
        assert "vibe" in str(refusal.value)

    def test_the_reading_half_is_read_from_the_validator_not_restated(
            self, tmp_path):
        packet = the_brief(records_tree(tmp_path))
        assert packet["reading"] == {
            "schema": us.READING_SCHEMA,
            "claim_fields": list(us.CLAIM_PRODUCED),
            "kinds": list(us.KINDS),
            "major_changes": list(us.MAJOR_CHANGES)}

    def test_every_key_of_a_brief_has_a_producer(self, tmp_path):
        packet = the_brief(records_tree(tmp_path))
        assert sorted(packet) == sorted(brief.BRIEF_PRODUCED)
        assert sorted(packet["card"]) == sorted(brief.CARD_PRODUCED)
        assert sorted(packet["material"]) == sorted(brief.MATERIAL_PRODUCED)


# --------------------------------------------------------------------------
# 5. selection and the packet file
# --------------------------------------------------------------------------

class TestSelectingAndWriting:

    def test_a_selection_that_matches_nothing_names_what_is_on_offer(self, tmp_path):
        records = records_tree(tmp_path)
        with pytest.raises(brief.Refused) as refusal:
            brief.select(briefs_for(records), card="information/elsewhere.md")
        assert "elsewhere.md" in str(refusal.value)
        assert CARD in str(refusal.value)

    def test_an_empty_understanding_layer_is_a_different_refusal(self, tmp_path):
        with pytest.raises(brief.Refused) as refusal:
            brief.select([], card=CARD)
        assert "proposes no range" in str(refusal.value)

    def test_a_range_that_cannot_be_pinned_is_listed_not_silently_dropped(
            self, tmp_path):
        """Two lists, and the refused one is the point.

        The document row below is a real range row with its two positions and
        its kind changed — the shape `understanding_delta` emits for a document
        citation, which is the shape a project card on this node really carries
        beside its chat citations. A listing that raised on it would have taken
        the chat range down with it.
        """
        records = records_tree(tmp_path)
        report = report_for(records)
        card_row = next(item for item in report["cards"] if item["card"] == CARD)
        document = dict(next(iter(card_row["judged"]["review_ranges"])))
        document.update({"kind": "WHOLE_DOCUMENT", "from_position": None,
                         "to_position": None, "manifest": "wiki/content.md"})
        card_row = dict(card_row)
        card_row["judged"] = dict(card_row["judged"])
        card_row["judged"]["review_ranges"] = [
            *_report_ranges(card_row), document]
        pinned, refused = brief.briefs({"cards": [card_row]}, records)
        assert len(pinned) == 1
        assert len(refused) == 1
        assert refused[0]["manifest"] == "wiki/content.md"
        assert "document body" in refused[0]["why"]

    def test_every_range_unpinnable_is_a_different_refusal_from_no_range(
            self, tmp_path):
        records = records_tree(tmp_path)
        refused = unpinnable_for(records)
        with pytest.raises(brief.Refused) as refusal:
            brief.select([], refused=refused or [{"card": CARD, "kind": "X",
                                                  "why": "no"}], card=CARD)
        assert "cannot pin any of them" in str(refusal.value)

    def test_a_position_selects_the_range_that_contains_it(self, tmp_path):
        records = records_tree(tmp_path)
        packet = the_brief(records)
        low = packet["source"]["from_position"]
        assert len(brief.select(briefs_for(records), position=low)) == 1
        with pytest.raises(brief.Refused):
            brief.select(briefs_for(records), position=low - 1)

    def test_a_packet_is_written_absolutely_and_leaves_no_half_file(self, tmp_path):
        packet = the_brief(records_tree(tmp_path))
        target = tmp_path / "out" / "packet.json"
        brief.write(packet, target)
        assert json.loads(target.read_text(encoding="utf-8")) == packet
        assert [item.name for item in target.parent.iterdir()] == ["packet.json"]

    def test_a_relative_packet_path_is_refused(self, tmp_path):
        packet = the_brief(records_tree(tmp_path))
        with pytest.raises(brief.Refused) as refusal:
            brief.write(packet, Path("packet.json"))
        assert "not absolute" in str(refusal.value)

    def test_the_same_packet_is_the_same_bytes(self, tmp_path):
        records = records_tree(tmp_path)
        assert brief.dumps(briefs_for(records)[0]) == brief.dumps(
            briefs_for(records)[0])


# --------------------------------------------------------------------------
# 6. the verb
# --------------------------------------------------------------------------

class TestTheVerb:

    def test_the_verb_prints_the_material_a_session_will_read(self, tmp_path):
        records = records_tree(tmp_path)
        result = cli("work", "understanding-brief", "--records-root", records)
        assert result.returncode == 0, result.stderr
        assert "第5条" in result.stdout
        assert CARD in result.stdout

    def test_the_verb_writes_one_packet_and_says_where(self, tmp_path):
        records = records_tree(tmp_path)
        target = tmp_path / "packet.json"
        result = cli("work", "understanding-brief", "--records-root", records,
                     "--card", CARD, "--write", target, "--json")
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["count"] == 1
        assert json.loads(target.read_text(encoding="utf-8")) == payload["briefs"][0]

    def test_write_refuses_a_selection_that_is_not_exactly_one(self, tmp_path):
        """A packet file holding several ranges is one a session cannot tell it
        read, and the refusal says which flag narrows it."""
        records = records_tree(tmp_path)
        (records / "information/understanding/people/y.md").write_text(
            card_prose(), encoding="utf-8")
        result = cli("work", "understanding-brief", "--records-root", records,
                     "--write", tmp_path / "packet.json")
        assert result.returncode != 0
        assert "--card/--manifest/--position" in result.stderr
        assert not (tmp_path / "packet.json").exists()

    def test_a_relative_records_root_is_refused(self, tmp_path):
        result = cli("work", "understanding-brief", "--records-root", "records")
        assert result.returncode != 0
        assert "must be absolute" in result.stderr

    def test_the_verb_authors_no_reading(self, tmp_path):
        """No model client, no clock and no writes into the records tree."""
        records = records_tree(tmp_path)
        before = {path: path.read_bytes() for path in records.rglob("*")
                  if path.is_file()}
        result = cli("work", "understanding-brief", "--records-root", records,
                     "--json")
        assert result.returncode == 0, result.stderr
        after = {path: path.read_bytes() for path in records.rglob("*")
                 if path.is_file()}
        assert after == before

    def test_the_module_holds_no_model_client(self):
        source = (REPO_ROOT / "intake" / "understanding_brief.py").read_text(
            encoding="utf-8")
        for forbidden in ("openai", "anthropic", "litellm", "requests", "urlopen",
                          "subprocess"):
            assert forbidden not in source


# --------------------------------------------------------------------------
# 7. the authority: a recorded grant, not a caller's file
# --------------------------------------------------------------------------

class TestTheAuthorityIsResolved:

    def commit(self, tmp_path, env, records, *, grant, now=NOW):
        packet = the_brief(records)
        reading = write_reading(tmp_path, reading_from(packet))
        return cli("work", "understanding-semantic", "--records-root", records,
                   "--reading", reading, "--commit", "--committed-at", STAMP,
                   "--now", now, "--grant", grant, "--json", env_extra=env)

    def test_a_recorded_grant_authorizes_the_reading(self, tmp_path):
        env = seeded(tmp_path)
        records = records_tree(tmp_path)
        result = self.commit(tmp_path, env, records, grant="grant:x")
        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        assert receipt["state"] == "COMPLETE"
        # One write per consumer: the card the claim belongs to and the view it
        # was told to appear in. Both, or the claim is in a card no reader of
        # the view will see.
        written = {row["role"]: row["claims"] for row in receipt["written"]}
        assert written == {"OWNER": ["c1"], "VIEW": ["c1"]}

    def test_a_grant_nothing_recorded_delegates_nothing(self, tmp_path):
        env = seeded(tmp_path)
        records = records_tree(tmp_path)
        result = self.commit(tmp_path, env, records, grant="grant:never")
        assert result.returncode != 0
        assert "not a grant this node recorded" in result.stderr
        assert (records / CARD).read_text(encoding="utf-8") == card_prose()

    def test_a_revoked_grant_does_not_authorize_while_its_root_still_does(
            self, tmp_path):
        """The asymmetry that made this check necessary: `derivation` judges the
        grant's ROOT and says nothing about revocation, so a reader that only
        asked it would report a revoked grant as AUTHORIZED."""
        env = seeded(tmp_path)
        store = store_mod.AgentControlStore(
            Path(env["SERVEROS_STATE_DIR"]) / "runtime" / "agent-control"
            / "store.json")
        store.revoke_grant("grant:x", at=NOW, reason="the delegation ended")
        records = records_tree(tmp_path)
        result = self.commit(tmp_path, env, records, grant="grant:x")
        assert result.returncode != 0
        assert "MISSING_OR_REVOKED_GRANT" in result.stderr
        assert (records / CARD).read_text(encoding="utf-8") == card_prose()

    def test_the_root_alone_would_still_authorize_that_same_grant(self, tmp_path):
        """Where the revocation check comes from, asserted against the module
        the CLI could have relied on instead.

        `context/delegation.derivation` judges the ROOT and assigns the grant's
        own scope and window to `intake/daily_cycle.grant_reason`; it says
        nothing about `revoked`. This reads the revoked grant through it and
        gets AUTHORIZED, which is exactly why the verb may not stop there.
        """
        env = seeded(tmp_path)
        store = store_mod.AgentControlStore(
            Path(env["SERVEROS_STATE_DIR"]) / "runtime" / "agent-control"
            / "store.json")
        store.revoke_grant("grant:x", at=NOW)
        delegation = _load_path("serveros_context_delegation",
                                REPO_ROOT / "context" / "delegation.py")
        verdict = delegation.derivation(store.grants()["grant:x"], "grant:x",
                                        store.contexts()[CONTEXT_REF], now=NOW)
        assert verdict["state"] == "AUTHORIZED"
        records = records_tree(tmp_path)
        assert self.commit(tmp_path, env, records, grant="grant:x").returncode != 0

    def test_a_grant_whose_window_has_passed_does_not_authorize(self, tmp_path):
        env = seeded(tmp_path, grant=a_grant(
            expires_at="2026-09-23T01:00:00+00:00"))
        records = records_tree(tmp_path)
        result = self.commit(tmp_path, env, records, grant="grant:x")
        assert result.returncode != 0
        assert "GRANT_NOT_CURRENT" in result.stderr

    def test_a_grant_whose_root_is_stale_does_not_authorize(self, tmp_path):
        env = seeded(tmp_path)
        records = records_tree(tmp_path)
        result = self.commit(tmp_path, env, records, grant="grant:x",
                             now="2026-09-23T06:00:00+00:00")
        assert result.returncode != 0
        assert "DELEGATION_DOES_NOT_AUTHORIZE" in result.stderr
        assert (records / CARD).read_text(encoding="utf-8") == card_prose()

    def test_a_grant_that_records_no_covers_covers_nothing(self, tmp_path):
        """The safe direction, and it is not an error: every claim lands as a
        candidate awaiting a person rather than committing on a scope nobody
        wrote down."""
        env = seeded(tmp_path)
        store = store_mod.AgentControlStore(
            Path(env["SERVEROS_STATE_DIR"]) / "runtime" / "agent-control"
            / "store.json")
        store.put_grant(a_grant(grant_ref="grant:bare", covers=None))
        records = records_tree(tmp_path)
        result = self.commit(tmp_path, env, records, grant="grant:bare")
        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        assert receipt["state"] == "NOTHING_TO_WRITE"
        assert receipt["written"] == []
        assert (records / CARD).read_text(encoding="utf-8") == card_prose()

    def test_a_covers_that_is_not_a_list_of_prefixes_is_refused(self, tmp_path):
        env = seeded(tmp_path)
        store = store_mod.AgentControlStore(
            Path(env["SERVEROS_STATE_DIR"]) / "runtime" / "agent-control"
            / "store.json")
        store.put_grant(a_grant(grant_ref="grant:odd", covers="x"))
        records = records_tree(tmp_path)
        result = self.commit(tmp_path, env, records, grant="grant:odd")
        assert result.returncode != 0
        assert "not a list of subject prefixes" in result.stderr

    def test_the_two_forms_of_authority_are_mutually_exclusive(self, tmp_path):
        result = cli("work", "understanding-semantic", "--reading",
                     tmp_path / "nothing.json", "--grant", "grant:x",
                     "--delegation", tmp_path / "nothing.json")
        assert result.returncode == 2
        assert "not allowed with" in result.stderr

    def test_a_clock_this_process_cannot_read_is_not_fallen_back_from(
            self, tmp_path):
        env = seeded(tmp_path)
        records = records_tree(tmp_path)
        result = self.commit(tmp_path, env, records, grant="grant:x",
                             now="yesterday afternoon")
        assert result.returncode != 0
        assert "is not a timestamp" in result.stderr

    def test_a_clock_with_no_timezone_names_no_instant(self, tmp_path):
        env = seeded(tmp_path)
        records = records_tree(tmp_path)
        result = self.commit(tmp_path, env, records, grant="grant:x",
                             now="2026-09-23T02:00:00")
        assert result.returncode != 0
        assert "no timezone" in result.stderr


# --------------------------------------------------------------------------
# 8. the chain: packet → reading → commit → recall, and a source that moves
# --------------------------------------------------------------------------

class TestTheChainThroughTheVerbs:

    def commit(self, tmp_path, env, records, reading, name="reading.json"):
        return cli("work", "understanding-semantic", "--records-root", records,
                   "--reading", write_reading(tmp_path, reading, name),
                   "--grant", "grant:x", "--commit", "--committed-at", STAMP,
                   "--now", NOW, "--json", env_extra=env)

    def recall(self, env, records, claim="c1"):
        result = cli("work", "claim-recall", "--records-root", records,
                     "--claim", claim, "--json", env_extra=env)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)["claims"][0]

    def test_the_chain_from_packet_to_recallable_claim(self, tmp_path):
        env = seeded(tmp_path)
        records = records_tree(tmp_path)
        packet = json.loads(cli(
            "work", "understanding-brief", "--records-root", records,
            "--card", CARD, "--json",
            env_extra=env).stdout)["briefs"][0]
        committed = self.commit(tmp_path, env, records, reading_from(packet))
        assert committed.returncode == 0, committed.stderr
        assert json.loads(committed.stdout)["state"] == "COMPLETE"
        claim = self.recall(env, records)
        assert claim["verdict"] == "CURRENT"
        assert claim["recallable"] is True

    def test_a_source_that_moves_makes_the_committed_claim_not_current(
            self, tmp_path):
        """The two halves of one sentence: the reading is refused AND what it
        wrote stops being current. A pin that only refused new readings would
        leave the old claim looking like the source still said it."""
        env = seeded(tmp_path)
        records = records_tree(tmp_path)
        packet = the_brief(records)
        commit = self.commit(tmp_path, env, records, reading_from(packet))
        assert commit.returncode == 0, commit.stderr

        edit_in_place(records)
        claim = self.recall(env, records)
        assert claim["verdict"] == "STALE"
        assert claim["recallable"] is False
        assert (claim["source_revision"]["recorded"]
                != claim["source_revision"]["current"])

    def test_a_retraction_written_through_the_packet_stops_the_claim(self, tmp_path):
        """§1 read through §3: the reading that withdraws a claim is a reading
        like any other, pinned to the same version and committed under the same
        resolved authority."""
        env = seeded(tmp_path)
        records = records_tree(tmp_path)
        packet = the_brief(records)

        # The retraction is a reading of the source at its NEW revision. One
        # span has one reading — a second reading of the same span under the
        # same content identity is refused by the receipt, deliberately — so
        # the record that changes the conclusion is a record the source
        # actually revised, which is also the only way a reader learns that the
        # older conclusion was wrong.
        first = self.commit(tmp_path, env, records, reading_from(packet))
        assert first.returncode == 0, first.stderr
        edit_in_place(records)
        current = the_brief(records)
        assert current["read"]["content_identity"] != packet["read"]["content_identity"]

        # Two claims in one reading, and the pair is the point: §1's
        # requirement (8) is that a new conclusion may supersede the old one
        # only by being stated, with its own support. A reading carrying the
        # RETRACTION alone withdraws the old conclusion and puts nothing in its
        # place — which is a legitimate reading (`RETRACTION_ONLY`, asserted
        # below) and a different one from this.
        retraction = reading_from(current, claims=[
            {"id": "c2", "kind": us.RETRACTION, "subject": "x",
             "assertion": {"was": "她说周五交付", "now": "她说的是下周"},
             "support": {"positions": [current["source"]["to_position"]],
                         "excerpt": "改过"},
             "applies_to": None, "touches": [], "supersedes": ["c1"],
             "conflicts": [], "card": CARD, "views": [VIEW]},
            {"id": "c3", "kind": us.OBSERVED_FACT, "subject": "x",
             "assertion": {"was": "她说周五交付", "now": "她说的是下周"},
             "support": {"positions": [current["source"]["to_position"]],
                         "excerpt": "改过"},
             "applies_to": None, "touches": [], "supersedes": [],
             "conflicts": [], "card": CARD, "views": [VIEW]}])
        second = self.commit(tmp_path, env, records, retraction, "second.json")
        assert second.returncode == 0, second.stderr
        receipt = json.loads(second.stdout)
        assert receipt["state"] == "COMPLETE"
        owner = {row["role"]: row for row in receipt["written"]}["OWNER"]
        # The retracted claim is STILL in the card, and that is right: a card
        # records what was believed and what was withdrawn, and a write that
        # deleted the older line would leave nothing for the receipt to be
        # evidence about.
        assert set(owner["claims"]) == {"c1", "c3"}
        assert owner["removed"] == []

        claim = self.recall(env, records)
        assert claim["verdict"] == "RETRACTED"
        assert claim["recallable"] is False
        # The evidence, not a label: a withdrawal with no receipt beside it
        # would be an assertion about the past that nothing recorded.
        assert claim["retraction"], "a withdrawal with no receipt is a label"
        assert claim["retraction"][0]["receipt"].endswith(".json")
        # Both halves of the supersession are readable afterwards: the
        # replacement is current and the card names what it replaced.
        assert self.recall(env, records, "c3")["verdict"] == "CURRENT"
        # And the card itself carries the transition, so a reader of the card
        # does not have to open the recall layer to learn what replaced what.
        assert "她说周五交付 → 她说的是下周" in (
            records / CARD).read_text(encoding="utf-8")

    def test_a_reading_that_only_withdraws_leaves_a_receipt_and_no_claim(
            self, tmp_path):
        """RETRACTION_ONLY is §1's state and it is not a failure: a retraction
        writes no sentence into any card — it removes one — while still leaving
        the receipt that makes the withdrawal durable. A verb that reported
        NOTHING_TO_WRITE here would leave the recall layer with no record that
        anything had been withdrawn."""
        env = seeded(tmp_path)
        records = records_tree(tmp_path)
        packet = the_brief(records)
        assert self.commit(tmp_path, env, records,
                           reading_from(packet)).returncode == 0
        edit_in_place(records)
        current = the_brief(records)
        only = reading_from(current, claims=[{
            "id": "c2", "kind": us.RETRACTION, "subject": "x",
            "assertion": {"was": "她说周五交付", "now": "她说的是下周"},
            "support": {"positions": [current["source"]["to_position"]],
                        "excerpt": "改过"},
            "applies_to": None, "touches": [], "supersedes": ["c1"],
            "conflicts": [], "card": CARD, "views": [VIEW]}])
        second = self.commit(tmp_path, env, records, only, "second.json")
        assert second.returncode == 0, second.stderr
        assert json.loads(second.stdout)["state"] == "RETRACTION_ONLY"
        assert self.recall(env, records)["verdict"] == "RETRACTED"


class TestDocumentReading:
    def packet(self, tmp_path):
        records = records_tree(tmp_path)
        folder = records / RAW / "links/doc"
        folder.mkdir(parents=True)
        body = "# Snapshot\nRedis is shared by two workers.\n"
        (folder / "content.md").write_text(body, encoding="utf-8")
        digest = hashlib.sha256(body.encode()).hexdigest()
        (folder / "manifest.json").write_text(json.dumps({
            "url": "https://example.test/doc", "type": "wiki",
            "content_sha256": digest, "media_count": 2, "media": ["one.png", "two.png"],
        }))
        (records / CARD).write_text(
            f"# X\n\n## Sources\n- `{RAW}/links/doc/content.md` sha deadbeef\n")
        card, row = card_and_range(records)
        return records, brief.brief_for(card, row, records)

    def test_exact_document_packet_reaches_the_existing_writer(self, tmp_path):
        records, packet = self.packet(tmp_path)
        assert packet["source"]["from_position"] is None
        assert packet["source"]["kind"] == "DOCUMENT"
        assert packet["material"]["schema"] == "serveros/source-reading-packet.v1"
        assert packet["material"]["revision"] is None
        assert packet["material"]["coverage"]["media_read"] == 0
        assert packet["material"]["coverage"]["media_declared"] == 2
        document = reading_from(packet)
        document["claims"][0]["support"] = {"positions": [], "excerpt": "Redis is shared by two workers."}
        reading = tmp_path / "reading.json"
        reading.write_text(json.dumps(document))
        result = cli("work", "understanding-semantic", "--records-root", records,
                     "--reading", reading, "--delegation", delegation_file(tmp_path), "--json")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["committed"][0]["id"] == "c1"

    def test_document_body_drift_is_refused_without_changing_records(self, tmp_path):
        records, packet = self.packet(tmp_path)
        document = reading_from(packet)
        document["claims"][0]["support"] = {"positions": [], "excerpt": "Redis is shared by two workers."}
        reading = tmp_path / "reading.json"
        reading.write_text(json.dumps(document))
        Path(packet["material"]["path"]).write_text("drifted body\n")
        before = (records / CARD).read_bytes()
        result = cli("work", "understanding-semantic", "--records-root", records,
                     "--reading", reading, "--delegation", delegation_file(tmp_path), "--json")
        assert result.returncode != 0
        assert "digest" in result.stderr
        assert (records / CARD).read_bytes() == before

    @pytest.mark.parametrize("support", [
        {"positions": [], "excerpt": "not in the snapshot"},
        {"positions": [1], "excerpt": "Redis is shared by two workers."},
    ])
    def test_document_citation_must_be_in_the_verified_body(self, tmp_path, support):
        records, packet = self.packet(tmp_path)
        document = reading_from(packet)
        document["claims"][0]["support"] = support
        reading = write_reading(tmp_path, document)
        result = cli("work", "understanding-semantic", "--records-root", records,
                     "--reading", reading, "--delegation", delegation_file(tmp_path), "--json")
        assert result.returncode != 0
        assert "excerpt" in result.stderr

    def test_document_commit_is_not_offered_for_another_reading(self, tmp_path):
        env = seeded(tmp_path)
        records, packet = self.packet(tmp_path)
        document = reading_from(packet)
        document["claims"][0]["support"] = {"positions": [], "excerpt": "Redis is shared by two workers."}
        reading = write_reading(tmp_path, document)
        result = cli("work", "understanding-semantic", "--records-root", records,
                     "--reading", reading, "--grant", "grant:x", "--now", NOW,
                     "--commit", "--committed-at", STAMP, "--json", env_extra=env)
        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        assert receipt["state"] == "COMPLETE"
        assert receipt["key"] == [CARD, packet["source"]["manifest"], None, None,
                                   packet["read"]["content_identity"]]
        assert brief.briefs(report_for(records), records) == ([], [])
        recall = _load("claim_recall", "claim_recall.py")
        result = recall.recall(records, only=["c1"])
        assert result["claims"][0]["verdict"] == "CURRENT"
        body = Path(packet["material"]["path"])
        body.write_text("The source now reports a single worker.\n")
        manifest = body.with_name("manifest.json")
        state = json.loads(manifest.read_text())
        state["content_sha256"] = hashlib.sha256(body.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(state))
        result = recall.recall(records, only=["c1"])
        assert result["claims"][0]["verdict"] == "STALE"
        pinned, refused = brief.briefs(report_for(records), records)
        assert len(pinned) == 1 and not refused
