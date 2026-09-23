"""The write half of B2 — what a patch may and may not say on its own.

Three properties carry the module, and each is tested against the thing it
would have to be true of rather than against a description of it:

  * a confirmation is authored input, so with no decisions file no item is
    CONFIRMED and no code path can produce one;
  * a decision covers the exact range it was made about, so a source that
    moves again makes the item AWAITING and files the decision as superseded;
  * every item carries the version pair it was computed from, so nothing in
    the patch has to be believed without a way to check it.

The fixtures are the reader's own: a capture, a messages file and a card, read
through `understanding_delta` exactly as the CLI reads them. Where a test needs
a report the reader would not produce today — a card proposing two ranges with
one identity — it says so at the helper, because testing what the writer does
when the reader misbehaves is the point and a fabricated report is how to do it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "runtime" / "cli.py"
LIVE_RECORDS = Path("/data/home/vince/workspace/records")


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(
        "serveros_intake_" + name, REPO_ROOT / "intake" / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = _load("capture_coverage", "capture_coverage.py")
ud = _load("understanding_delta", "understanding_delta.py")
up = _load("understanding_patch", "understanding_patch.py")


def cli(*args, cwd=None) -> subprocess.CompletedProcess:
    """Run the real verb. `cwd` defaults to the tree, as a person would run it.

    It is a parameter because one test has to run from somewhere else: the
    relative-destination refusal, whose failure mode is a stray file in the
    working directory. See that test.
    """
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True,
                          cwd=str(cwd or REPO_ROOT))


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

CHAT = "information/raw/feishu/chats/people/x/oc_x/messages.jsonl"
DOC = "information/raw/feishu/chats/people/x/oc_x/links/d/content.md"
RAW = Path("information/raw/feishu/chats")


def chat_manifest(**over) -> dict:
    document = {
        "contact": {"name": "X", "open_id": "ou_x"},
        "chat": {"chat_id": "oc_x", "chat_type": "p2p", "name": "X"},
        "sync": {
            "mode": "incremental", "method": "im/chat-messages-list",
            "msg_count": 10, "remote_unique_message_count": 10,
            "fetched_page_count": 1, "pagination_complete": True,
            "earliest_create_time": "2026-09-11 09:45",
            "latest_create_time": "2026-09-11 10:07",
            "last_message_id": "om_x",
        },
    }
    document.update(over)
    return document


def document_manifest(**over) -> dict:
    document = {
        "source_message_id": "om_x", "url": "https://example.invalid/d",
        "type": "docx", "title": "T", "fetched_at": "2026-09-07",
        "content_sha256": "a" * 64, "redaction_applied": False,
    }
    document.update(over)
    return document


@pytest.fixture
def tree(tmp_path):
    records = tmp_path / "records"
    for relative in ("information/understanding/people",
                     "information/raw/feishu/chats"):
        (records / relative).mkdir(parents=True)
    return records


def write_capture(records: Path, relative: str, document) -> Path:
    path = records / RAW / relative / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def write_messages(records: Path, relative: str, rows: list[dict]) -> Path:
    path = records / RAW / relative / "messages.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
                    + "\n", encoding="utf-8")
    return path


def messages(count: int, *, edited: tuple = (), deleted: tuple = ()) -> list[dict]:
    return [{"message_position": str(position), "message_id": f"om_{position}",
             "content": "x", "updated": position in edited,
             "deleted": position in deleted}
            for position in range(count)]


def recontent(records: Path, relative: str, position: int, text: str) -> Path:
    """Rewrite one message's body, leaving its position and its flags alone.

    The collector's `updated` flag is not the only way a span's content
    changes: a capture re-read from the source can carry different text at the
    same position with the same flags. That is exactly the case a decision
    keyed on position alone would inherit — the range did not move, so nothing
    about the proposal's PLACE looks different.
    """
    path = records / RAW / relative / "messages.jsonl"
    rows = [json.loads(line) for line
            in path.read_text(encoding="utf-8").splitlines() if line]
    for row in rows:
        if row["message_position"] == str(position):
            row["content"] = text
    return write_messages(records, relative, rows)


def write_card(records: Path, scope: str, name: str, body: str) -> Path:
    path = records / "information" / "understanding" / scope / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def heading_card(sources: str) -> str:
    return "# 卡\n\n## 当前\n\n内容。\n\n## Sources\n" + sources


def read(records: Path) -> dict:
    objects = cc.read_root(records)["objects"]
    return ud.read_and_reconcile(records, objects)


@pytest.fixture
def behind(tmp_path):
    """A records tree whose one card is behind its one source by five messages."""
    records = tmp_path / "records"
    for relative in ("information/understanding/people",
                     "information/raw/feishu/chats"):
        (records / relative).mkdir(parents=True)
    write_capture(records, "people/x/oc_x", chat_manifest())
    write_messages(records, "people/x/oc_x", messages(10))
    write_card(records, "people", "x.md",
               heading_card(f"- `{CHAT}` message_position 0-4\n"))
    return records


def patch_of(records: Path, decisions=None) -> dict:
    return up.build(read(records), decisions or {})


def item_of(records: Path) -> dict:
    patch = patch_of(records)
    assert len(patch["items"]) == 1, patch["items"]
    return patch["items"][0]


def decisions_file(tmp_path: Path, *entries: dict) -> Path:
    path = tmp_path / "decisions.jsonl"
    path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n"
                            for entry in entries), encoding="utf-8")
    return path


def decision_for(item: dict, state: str = up.CONFIRMED, **over) -> dict:
    """A decision copied off an item, the way a person would write one.

    Every identity field the item carries is copied, not retyped: the two
    digests are what say WHICH revision was read, and a test that invented
    them would be testing a decision nobody could have authored.
    """
    entry = {field: item.get(field) for field in up.IDENTITY_FIELDS}
    entry.update({"decision": state, "by": "张正己",
                  "at": "2026-09-22 11:00"})
    entry.update(over)
    return entry


def keys_in(node) -> set[str]:
    """Every key anywhere in a nested record, so `claim` cannot hide in a sub-dict."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            found |= keys_in(value)
    elif isinstance(node, list):
        for value in node:
            found |= keys_in(value)
    return found


# --------------------------------------------------------------------------
# a proposal, never a confirmation
# --------------------------------------------------------------------------

class TestThePatchProposesAndNeverConfirms:
    def test_with_no_decisions_every_item_is_awaiting(self, behind):
        item = item_of(behind)
        assert item["confirmation"] == up.AWAITING
        assert item["decided_by"] is None and item["decided_at"] is None
        assert patch_of(behind)["confirmed"] == 0

    def test_an_item_carries_both_versions_it_was_computed_from(self, behind):
        item = item_of(behind)
        assert item["review"] == "BEYOND_APPLIED"
        assert (item["from_position"], item["to_position"]) == (5, 9)
        assert item["applied"] == 4           # where the CARD had got to
        assert item["source_max"] == 9        # where the SOURCE had got to
        assert item["source_count"] == 10
        assert item["source"] == CHAT
        assert item["manifest"] == "feishu/chats/people/x/oc_x/manifest.json"

    def test_a_document_range_carries_digests_and_no_positions(self, tmp_path):
        records = tmp_path / "records"
        for relative in ("information/understanding/projects",
                         "information/raw/feishu/chats"):
            (records / relative).mkdir(parents=True)
        # The document's own manifest carries the digest, not the chat's: the
        # capture for `links/d/` is a separate object from the conversation it
        # arrived in, and the reader compares the citation against that one.
        write_capture(records, "people/x/oc_x/links/d",
                      document_manifest(content_sha256="f" * 64))
        write_card(records, "projects", "p.md",
                   heading_card(f"- `{DOC}`（sha `beef1234`）\n"))
        item = item_of(records)
        assert item["review"] == "WHOLE_DOCUMENT"
        assert item["from_position"] is None and item["to_position"] is None
        assert item["cited_digest"] == "beef1234"
        assert item["content_sha256"] == "f" * 64

    def test_nothing_anywhere_in_the_patch_is_called_a_claim(self, behind):
        """The module reads no content, so it may not name a content assertion.

        `understanding_delta`'s own tests hold the same rule for the reader. The
        design document calls this a Claim patch; the record deliberately does
        not, because every field here is a version, a range or a count.
        """
        for forbidden in ("claim", "understanding", "summary_of", "user_confirmed",
                          "asserted", "conclusion"):
            assert forbidden not in keys_in(patch_of(behind)), forbidden
        # `confirmation` and the `confirmed` COUNT are both here on purpose and
        # are not the thing this test forbids: one is the state of a proposal,
        # the other counts decisions a person wrote down. Neither asserts that
        # this module understood anything.
        assert keys_in(patch_of(behind)) & {"confirmation", "confirmed"} == {
            "confirmation", "confirmed"}

    def test_the_render_says_awaiting_and_never_confirmed(self, behind):
        lines = up.render(patch_of(behind))
        text = "\n".join(lines)
        assert "待确认 1" in text
        assert "已确认" not in text
        assert "AWAITING" in text


# --------------------------------------------------------------------------
# a confirmation is authored input
# --------------------------------------------------------------------------

class TestAConfirmationIsAuthoredInput:
    def test_a_decision_marks_one_item_confirmed_and_names_who(self, behind, tmp_path):
        item = item_of(behind)
        path = decisions_file(tmp_path, decision_for(item))
        patch = up.build(read(behind), up.read_decisions(path), str(path))
        assert patch["confirmed"] == 1 and patch["awaiting"] == 0
        assert patch["items"][0]["decided_by"] == "张正己"
        assert patch["items"][0]["decided_at"] == "2026-09-22 11:00"
        assert patch["decisions_path"] == str(path)

    def test_a_rejection_is_carried_as_a_rejection(self, behind, tmp_path):
        item = item_of(behind)
        patch = up.build(read(behind),
                         up.read_decisions(decisions_file(
                             tmp_path, decision_for(item, up.REJECTED))))
        assert patch["rejected"] == 1 and patch["items"][0]["confirmation"] == up.REJECTED

    def test_awaiting_is_not_a_decision_a_person_can_write_down(self, behind, tmp_path):
        with pytest.raises(up.Malformed) as caught:
            up.read_decisions(decisions_file(tmp_path,
                                             decision_for(item_of(behind),
                                                          up.AWAITING)))
        assert "absence of a decision" in str(caught.value)

    def test_a_confirmation_nobody_is_named_for_is_malformed(self, behind, tmp_path):
        entry = decision_for(item_of(behind))
        del entry["by"]
        with pytest.raises(up.Malformed) as caught:
            up.read_decisions(decisions_file(tmp_path, entry))
        assert "by must be a non-empty string" in str(caught.value)

    def test_a_decision_field_the_table_does_not_name_is_malformed(self, behind, tmp_path):
        entry = decision_for(item_of(behind), note="我也顺手改了一下卡")
        with pytest.raises(up.Malformed) as caught:
            up.read_decisions(decisions_file(tmp_path, entry))
        assert "unexpected field" in str(caught.value)

    def test_two_decisions_about_one_proposal_are_refused(self, behind, tmp_path):
        item = item_of(behind)
        path = decisions_file(tmp_path, decision_for(item),
                              decision_for(item, up.REJECTED))
        with pytest.raises(up.Malformed) as caught:
            up.read_decisions(path)
        assert "which one a reader should act on" in str(caught.value)

    def test_a_malformed_line_names_its_line_number(self, tmp_path):
        path = tmp_path / "d.jsonl"
        path.write_text('{"card": "c"}\n', encoding="utf-8")
        with pytest.raises(up.Malformed) as caught:
            up.read_decisions(path)
        assert ":1:" in str(caught.value)

    def test_a_broken_line_is_told_apart_from_an_unreadable_file(self, tmp_path):
        path = tmp_path / "d.jsonl"
        path.write_text("{not json}\n", encoding="utf-8")
        with pytest.raises(up.Malformed) as caught:
            up.read_decisions(path)
        assert "invalid JSON" in str(caught.value)


# --------------------------------------------------------------------------
# a decision covers the range it was made about, and nothing else
# --------------------------------------------------------------------------

class TestADecisionDoesNotFollowTheCard:
    def test_a_decision_for_a_range_that_moved_is_superseded_not_applied(
            self, behind, tmp_path):
        """The card is confirmed where it stood, then the source moves again.

        The confirmation was made about `5–9`. The source now holds twelve
        messages, so the reader proposes `5–11`, and the decision is not about
        that. It is reported as superseded rather than dropped: a confirmation
        that silently stopped applying is worse than one never made.
        """
        item = item_of(behind)
        path = decisions_file(tmp_path, decision_for(item))
        decisions = up.read_decisions(path)
        write_messages(behind, "people/x/oc_x", messages(12))
        patch = up.build(read(behind), decisions, str(path))
        assert patch["items"][0]["confirmation"] == up.AWAITING
        assert patch["confirmed"] == 0
        assert [entry["decision"] for entry in patch["stale_decisions"]] == ["CONFIRMED"]
        assert patch["stale_decisions"][0]["by"] == "张正己"
        assert patch["stale_decisions"][0]["to_position"] == 9
        assert "not a decision about what is proposed now" in \
            patch["stale_decisions"][0]["why"]

    def test_the_same_span_holding_different_content_supersedes_its_decision(
            self, behind, tmp_path):
        """The counterexample the position-only identity could not survive.

        The card is confirmed about `5–9`. Then one message inside that span is
        rewritten — same path, same span, same number of messages, and the
        collector's `updated` flag never set. The proposal's PLACE is
        identical; only its content is not. A decision keyed on the place would
        still be counted as satisfied, recording 张正己 as having confirmed
        text they never read, which is the whole defect.
        """
        item = item_of(behind)
        decisions = up.read_decisions(decisions_file(tmp_path, decision_for(item)))
        before = patch_of(behind, decisions)
        assert before["confirmed"] == 1

        recontent(behind, "people/x/oc_x", 7, "改过的一句话")
        after = patch_of(behind, decisions)

        assert after["items"][0]["confirmation"] == up.AWAITING
        assert after["confirmed"] == 0
        assert after["stale_decisions"][0]["decision"] == up.CONFIRMED
        assert after["stale_decisions"][0]["by"] == "张正己"
        assert "the content this range names changed" in \
            after["stale_decisions"][0]["why"]
        # The place is unchanged, and the test says so: an assertion that only
        # saw the refusal would also pass if the writer had keyed on something
        # that merely happened to move at the same time.
        assert after["items"][0]["card_digest"] == before["items"][0]["card_digest"]
        assert after["items"][0]["from_position"] == item["from_position"]
        assert after["items"][0]["to_position"] == item["to_position"]
        assert after["items"][0]["content_identity"] != item["content_identity"]

    def test_a_card_rewritten_under_its_own_path_supersedes_its_decision(
            self, behind, tmp_path):
        """The other version half: the understanding baseline, not the source.

        Same path, same cited span, same source content — only the card's own
        words changed. A decision keyed on the path would follow the card
        through the rewrite; the digest is what makes the rewritten card a
        different proposal.
        """
        item = item_of(behind)
        decisions = up.read_decisions(decisions_file(tmp_path, decision_for(item)))
        write_card(behind, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-4\n\n补了一句话。\n"))
        patch = up.build(read(behind), decisions, None)
        assert patch["items"][0]["from_position"] == item["from_position"]
        assert patch["items"][0]["confirmation"] == up.AWAITING
        assert "the card's own text changed" in patch["stale_decisions"][0]["why"]
        assert "the content this range names changed" not in \
            patch["stale_decisions"][0]["why"]

    def test_a_rewrite_of_both_is_reported_as_both(self, behind, tmp_path):
        """Two reasons, two next actions, and neither is allowed to hide one.

        The reasons are joined rather than chosen between, because a person
        whose confirmation lapsed for two separate reasons would otherwise be
        told about one of them.
        """
        item = item_of(behind)
        decisions = up.read_decisions(decisions_file(tmp_path, decision_for(item)))
        write_card(behind, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-4\n\n补了一句话。\n"))
        recontent(behind, "people/x/oc_x", 7, "改过的一句话")
        patch = up.build(read(behind), decisions, None)
        why = patch["stale_decisions"][0]["why"]
        assert "the card's own text changed" in why
        assert "the content this range names changed" in why

    def test_a_decision_that_names_a_place_and_not_a_revision_is_malformed(
            self, behind, tmp_path):
        """Absent is not null.

        A decision with no `content_identity` key at all is a decision about a
        path, and the writer will not silently treat it as one. `null` is a
        different answer: it is a decision about a span whose content the
        reader could not identify, and it is accepted — with a reason — because
        the proposal itself carried null. The two cases are told apart by
        presence, and this test holds both halves so that widening one does not
        quietly widen the other.
        """
        for field in ("card_digest", "content_identity"):
            entry = decision_for(item_of(behind))
            del entry[field]
            with pytest.raises(up.Malformed) as caught:
                up.read_decisions(decisions_file(tmp_path, entry))
            assert f"`{field}` is absent" in str(caught.value)
            assert "names a place and not a revision" in str(caught.value)

        item = item_of(behind)
        entry = decision_for(item, content_identity=None)
        # `null` reads, because at the moment the file is read there is no
        # proposal to compare it against — the writer cannot see one, and a
        # writer that refused here would be refusing an input it has not
        # looked at. What it does instead is decline to APPLY it.
        decisions = up.read_decisions(decisions_file(tmp_path, entry))
        patch = up.build(read(behind), decisions, None)
        assert patch["items"][0]["confirmation"] == up.AWAITING
        assert patch["stale_decisions"][0]["decision"] == up.CONFIRMED

    def test_a_decision_is_keyed_on_the_versions_even_when_nothing_moves(
            self, behind, tmp_path):
        """The negative half: an unchanged tree still satisfies its decision.

        Without this, both counterexamples above would pass for a writer that
        had simply stopped applying decisions at all.
        """
        item = item_of(behind)
        decisions = up.read_decisions(decisions_file(tmp_path, decision_for(item)))
        first = up.build(read(behind), decisions, None)
        second = up.build(read(behind), decisions, None)
        assert first["confirmed"] == second["confirmed"] == 1
        assert first["stale_decisions"] == second["stale_decisions"] == []

    def test_a_card_rewritten_to_a_new_reach_does_not_inherit_a_confirmation(
            self, behind, tmp_path):
        item = item_of(behind)
        decisions = up.read_decisions(decisions_file(tmp_path, decision_for(item)))
        write_card(behind, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-8\n"))
        patch = up.build(read(behind), decisions, None)
        assert patch["items"][0]["from_position"] == 9
        assert patch["items"][0]["confirmation"] == up.AWAITING
        assert len(patch["stale_decisions"]) == 1

    def test_a_card_that_becomes_current_supersedes_its_confirmation(
            self, behind, tmp_path):
        item = item_of(behind)
        decisions = up.read_decisions(decisions_file(tmp_path, decision_for(item)))
        write_card(behind, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-9\n"))
        patch = up.build(read(behind), decisions, None)
        assert patch["items"] == []
        assert patch["stale_decisions"][0]["decision"] == "CONFIRMED"

    def test_a_decision_about_the_same_positions_in_another_capture_is_not_it(
            self, behind, tmp_path):
        """The identity carries the capture object, not just the cited path.

        One card may cite a path that several captures match; a decision about
        what one of them holds is not a decision about the other. The pair is
        the test: two decisions that differ in nothing but the capture object
        they name, one applied and one not. Asserting only the refusal would
        pass for any reason at all, including a decision the writer cannot key
        — which is exactly what a first version of this test did.
        """
        item = item_of(behind)
        path = decisions_file(tmp_path, decision_for(
            item, manifest="feishu/chats/groups/g/oc_g/manifest.json"))
        patch = up.build(read(behind), up.read_decisions(path), None)
        assert patch["items"][0]["confirmation"] == up.AWAITING
        assert len(patch["stale_decisions"]) == 1

        path = decisions_file(tmp_path, decision_for(item))
        patch = up.build(read(behind), up.read_decisions(path), None)
        assert patch["items"][0]["confirmation"] == up.CONFIRMED
        assert patch["stale_decisions"] == []

    def test_the_identity_is_the_card_the_capture_the_range_and_its_versions(
            self, behind):
        """Seven fields, and the last two are the point of the other five.

        A place and a revision are not the same thing, and the digests are
        what tell them apart. Asserting the field list rather than a literal
        tuple keeps this test from passing while `IDENTITY_FIELDS` and the
        reader's own key drift apart, which is the failure it exists to catch.
        """
        item = item_of(behind)
        assert up.item_key(item) == tuple(item[field]
                                          for field in up.IDENTITY_FIELDS)
        assert up.IDENTITY_FIELDS == ("card", "manifest", "review",
                                      "from_position", "to_position",
                                      "card_digest", "content_identity")


# --------------------------------------------------------------------------
# the denominator, kept beside the proposals
# --------------------------------------------------------------------------

class TestTheCountsAreBesideTheProposals:
    def test_cards_read_is_the_readers_own_card_count(self, behind):
        report = read(behind)
        assert patch_of(behind)["cards_read"] == len(report["cards"])

    def test_the_state_census_is_the_readers_own(self, behind):
        assert patch_of(behind)["states"] == read(behind)["summary"]["by_state"]

    def test_a_card_with_nothing_to_do_is_counted_with_a_reason(self, tmp_path):
        records = tmp_path / "records"
        for relative in ("information/understanding/people",
                         "information/raw/feishu/chats"):
            (records / relative).mkdir(parents=True)
        write_card(records, "people", "x.md", "# 卡\n\n没有任何出处的一段话。\n")
        patch = patch_of(records)
        assert patch["items"] == []
        assert patch["no_item"] == {"UNTRACEABLE": 1}
        assert "cites no raw path" in up.NO_ITEM_BECAUSE["UNTRACEABLE"]
        assert "1 份 UNTRACEABLE 无提议" in "\n".join(up.render(patch))

    def test_every_declared_state_says_what_it_produces(self):
        """Each state either yields items or has a written reason it does not.

        The two sets must together cover `CARD_STATES`, or a card could sit in
        a state that is neither proposed for nor explained — the shape of a
        silent drop that no count would show.
        """
        covered = set(up.ACTIONABLE) | set(up.NO_ITEM_BECAUSE)
        assert set(ud.CARD_STATES) <= covered, set(ud.CARD_STATES) - covered

    def test_a_state_that_proposes_and_offers_no_range_is_a_reader_gap(self, tmp_path):
        """A report the reader does not produce today, and the point of saying so.

        If the reader ever declared a card behind and proposed nothing, the
        patch must not count it as settled. It lands in `no_item` with the
        reason the module has no reason for it.
        """
        records = tmp_path / "records"
        for relative in ("information/understanding/people",
                         "information/raw/feishu/chats"):
            (records / relative).mkdir(parents=True)
        write_capture(records, "people/x/oc_x", chat_manifest())
        write_messages(records, "people/x/oc_x", messages(6))
        write_card(records, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-2\n"))
        report = read(records)
        report["cards"][0]["judged"]["review_ranges"] = []
        patch = up.build(report)
        assert patch["items"] == []
        assert patch["no_item"] == {"BEHIND": 1}


# --------------------------------------------------------------------------
# one identity, one proposal
# --------------------------------------------------------------------------

class TestOneIdentityIsOneProposal:
    def test_a_source_cited_twice_at_one_reach_is_one_proposal(self, tmp_path):
        records = tmp_path / "records"
        for relative in ("information/understanding/people",
                         "information/raw/feishu/chats"):
            (records / relative).mkdir(parents=True)
        write_capture(records, "people/x/oc_x", chat_manifest())
        write_messages(records, "people/x/oc_x", messages(10))
        write_card(records, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-4\n"
                                f"- `{CHAT}` message_position 0-4\n"))
        patch = patch_of(records)
        assert len(patch["items"]) == 1
        assert len(patch["skipped"]) == 1
        assert "more than once" in patch["skipped"][0]

    def test_two_proposals_cannot_share_an_identity_and_differ(self, tmp_path):
        """The reader does not do this today. If it did, neither may be emitted."""
        records = tmp_path / "records"
        for relative in ("information/understanding/people",
                         "information/raw/feishu/chats"):
            (records / relative).mkdir(parents=True)
        write_capture(records, "people/x/oc_x", chat_manifest())
        write_messages(records, "people/x/oc_x", messages(10))
        write_card(records, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-4\n"))
        report = read(records)
        twin = dict(report["cards"][0]["judged"]["review_ranges"][0])
        twin["cited_digest"] = "beefbeef"
        report["cards"][0]["judged"]["review_ranges"].append(twin)
        with pytest.raises(up.Refused) as caught:
            up.build(report)
        assert "share an identity" in str(caught.value)


# --------------------------------------------------------------------------
# every field has a producer, and the shape is the same for every kind
# --------------------------------------------------------------------------

class TestEveryEmittedFieldHasAProducer:
    def test_the_item_carries_exactly_the_keys_its_table_names(self, behind):
        assert set(item_of(behind)) == set(up.ITEM_PRODUCED)

    def test_the_patch_carries_exactly_the_keys_its_table_names(self, behind):
        assert set(patch_of(behind)) == set(up.PATCH_PRODUCED)

    def test_a_key_with_no_producer_is_a_refusal(self, behind, monkeypatch):
        monkeypatch.setattr(up, "ITEM_PRODUCED",
                            {k: v for k, v in up.ITEM_PRODUCED.items()
                             if k != "source_max"})
        with pytest.raises(up.Refused) as caught:
            up.build(read(behind))
        assert "item.source_max" in str(caught.value)

    def test_a_patch_key_with_no_producer_is_a_refusal(self, behind, monkeypatch):
        monkeypatch.setattr(up, "PATCH_PRODUCED",
                            {k: v for k, v in up.PATCH_PRODUCED.items()
                             if k != "cards_read"})
        with pytest.raises(up.Refused) as caught:
            up.build(read(behind))
        assert "patch.cards_read" in str(caught.value)

    def test_every_item_key_is_filled_for_every_range_kind(self, tmp_path):
        """One shape for all three kinds: the empty ones are present and null.

        A key that appears only for the kinds it applies to would make a
        consumer's field access a question about which kind it is holding.
        """
        shapes = []
        for kind in ("BEYOND_APPLIED", "REVISITED_IN_PLACE", "WHOLE_DOCUMENT"):
            records = tmp_path / kind
            for relative in ("information/understanding/people",
                             "information/raw/feishu/chats"):
                (records / relative).mkdir(parents=True)
            write_capture(records, "people/x/oc_x", chat_manifest())
            write_messages(records, "people/x/oc_x", messages(10))
            write_card(records, "people", "x.md",
                       heading_card(f"- `{CHAT}` message_position 0-4\n"))
            report = read(records)
            report["cards"][0]["judged"]["review_ranges"] = [{
                "card": report["cards"][0]["card"], "kind": kind, "source": CHAT,
                "manifest": "feishu/chats/people/x/oc_x/manifest.json",
                "from_position": 5, "to_position": 9, "applied": 4,
                "applied_low": 0, "source_max": 9, "source_count": 10,
                "latest_create_time": "2026-09-11 10:07", "last_message_id": "om_x",
            }]
            shapes.append(set(up.build(report)["items"][0]))
        assert shapes[0] == shapes[1] == shapes[2] == set(up.ITEM_PRODUCED)


# --------------------------------------------------------------------------
# no clock, one file, no second entry point
# --------------------------------------------------------------------------

class TestTheWriterHoldsNoClockAndWritesOneFile:
    def _tree(self):
        import ast
        return ast.parse((REPO_ROOT / "intake" /
                          "understanding_patch.py").read_text(encoding="utf-8"))

    def test_the_module_imports_no_clock(self):
        import ast
        imported = set()
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "time" not in imported and "datetime" not in imported

    def test_the_module_calls_no_clock(self):
        import ast
        called = {node.func.attr for node in ast.walk(self._tree())
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        for reader in ("now", "utcnow", "today", "monotonic", "time", "strftime"):
            assert reader not in called, reader

    def test_the_decisions_file_is_only_ever_read(self):
        """The whole load-bearing property, checked against the program.

        `read_decisions` opens one file and reads it. Every write in the module
        is inside `write`, which takes the patch path and has no argument that
        could be the decisions file. If a writer for the decisions path ever
        appears here, this test is where it stops.
        """
        import ast
        writers = {"open", "write_text", "write_bytes", "unlink", "rename",
                   "replace", "rmtree", "chmod", "touch", "mkdir"}
        offenders = []
        for node in ast.walk(self._tree()):
            if not isinstance(node, ast.FunctionDef):
                continue
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                    name = call.func.attr
                    modes = [keyword.value.value for keyword in call.keywords
                             if keyword.arg == "mode"
                             and isinstance(keyword.value, ast.Constant)]
                    modes += [arg.value for arg in call.args
                              if isinstance(arg, ast.Constant)
                              and isinstance(arg.value, str)]
                    if name in writers or (name == "open" and modes and "w" in modes[0]):
                        offenders.append((node.name, name, modes))
        assert offenders == [] or {name for name, _, _ in offenders} == {"write"}, offenders
        assert "read_text" in (REPO_ROOT / "intake" /
                               "understanding_patch.py").read_text(encoding="utf-8")

    def test_the_module_has_no_main_and_no_argparse(self):
        """`hasattr` for the entry point; `ast` for the import.

        Not a substring search for `argparse`: the module's own comment says
        there is no second argparse surface here, and a gate that reads text
        would find the sentence that promises the property and fail on it —
        the failure mode `docs/CC-EXECUTION-LEDGER.md` §5.0 records three
        times.
        """
        import ast
        assert not hasattr(up, "main")
        imported = set()
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "argparse" not in imported, imported

    def test_the_same_inputs_produce_the_same_bytes(self, behind):
        assert up.dumps(patch_of(behind)) == up.dumps(patch_of(behind))

    def test_serialisation_is_key_sorted(self, behind):
        text = up.dumps(patch_of(behind))
        assert text.endswith("\n")
        assert json.loads(text)["items"][0]["card"].startswith("information/")

    def test_write_leaves_the_file_and_no_temporary(self, behind, tmp_path):
        target = tmp_path / "out" / "patch.json"
        up.write(patch_of(behind), target)
        assert [path.name for path in target.parent.iterdir()] == ["patch.json"]

    def test_a_failed_write_leaves_no_temporary(self, behind, tmp_path, monkeypatch):
        target = tmp_path / "out" / "patch.json"

        def boom(*args):
            raise OSError("no space left on device")

        # A stand-in for the module's `os` rather than the real one: patching
        # the shared module would break whatever else is running in this
        # process, and the property under test is about this module's calls.
        monkeypatch.setattr(up, "os", types.SimpleNamespace(
            fdopen=os.fdopen, fsync=os.fsync, unlink=os.unlink, path=os.path,
            replace=boom))
        with pytest.raises(OSError):
            up.write(patch_of(behind), target)
        assert not target.exists()
        assert list(target.parent.iterdir()) == []

    def test_a_relative_destination_is_refused(self, behind, tmp_path, monkeypatch):
        """Run from a directory this test owns, so the refusal is the only thing
        that can keep a file out of the tree.

        The first version ran from the repository root, and the mutation that
        removes the guard wrote `patch.json` there — a test whose failure mode
        is litter in the checkout is a test that leaves evidence behind when the
        property it tests is broken. Standing in `tmp_path` makes the same
        mutation write into a directory pytest deletes, and the assertion below
        then says what the guard is for.
        """
        monkeypatch.chdir(tmp_path)
        with pytest.raises(up.Refused) as caught:
            up.write(patch_of(behind), Path("patch.json"))
        assert "not absolute" in str(caught.value)
        assert not (tmp_path / "patch.json").exists()

    def test_a_write_creates_its_directory_and_no_other(self, behind, tmp_path):
        target = tmp_path / "out" / "a" / "b" / "patch.json"
        up.write(patch_of(behind), target)
        assert target.is_file()
        assert sorted(path.name for path in (tmp_path / "out").iterdir()) == ["a"]


# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------

class TestVerify:
    def test_a_freshly_written_patch_verifies(self, behind, tmp_path):
        target = tmp_path / "patch.json"
        write(behind, target)
        assert up.verify(read(behind), {}, None, target) == []

    def test_an_item_added_by_hand_is_named(self, behind, tmp_path):
        target = tmp_path / "patch.json"
        write(behind, target)
        document = json.loads(target.read_text(encoding="utf-8"))
        document["items"].append({**document["items"][0], "from_position": 99})
        target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        reasons = up.verify(read(behind), {}, None, target)
        assert reasons and "no record derives" in reasons[0]

    def test_a_drifted_version_pair_is_named_field_by_field(self, behind, tmp_path):
        target = tmp_path / "patch.json"
        write(behind, target)
        document = json.loads(target.read_text(encoding="utf-8"))
        document["items"][0]["applied"] = 3
        target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        reasons = up.verify(read(behind), {}, None, target)
        assert reasons and "applied" in reasons[0] and "differ from the records" in reasons[0]

    def test_a_card_that_moved_makes_the_patch_stale(self, behind, tmp_path):
        target = tmp_path / "patch.json"
        write(behind, target)
        write_messages(behind, "people/x/oc_x", messages(12))
        reasons = up.verify(read(behind), {}, None, target)
        assert any("missing a proposal" in reason for reason in reasons), reasons

    def test_a_patch_from_another_decisions_file_is_not_this_one(self, behind, tmp_path):
        item = item_of(behind)
        path = decisions_file(tmp_path, decision_for(item))
        decisions = up.read_decisions(path)
        target = tmp_path / "patch.json"
        up.write(up.build(read(behind), decisions, str(path)), target)
        assert up.verify(read(behind), decisions, str(path), target) == []
        reasons = up.verify(read(behind), decisions, None, target)
        assert any("decisions_path" in reason for reason in reasons), reasons

    def test_an_unreadable_or_foreign_patch_returns_reasons_not_an_exception(
            self, behind, tmp_path):
        missing = tmp_path / "nope.json"
        assert "cannot read" in up.verify(read(behind), {}, None, missing)[0]
        wrong = tmp_path / "wrong.json"
        wrong.write_text(json.dumps({"schema": "other/v9"}), encoding="utf-8")
        assert "not 'serveros/understanding-patch.v1'" in \
            up.verify(read(behind), {}, None, wrong)[0]

    def test_a_hand_edit_to_the_census_is_named(self, behind, tmp_path):
        target = tmp_path / "patch.json"
        write(behind, target)
        document = json.loads(target.read_text(encoding="utf-8"))
        document["awaiting"] = 0
        target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        reasons = up.verify(read(behind), {}, None, target)
        assert any("awaiting" in reason for reason in reasons), reasons


def write(records: Path, target: Path) -> Path:
    return up.write(up.build(read(records)), target)


# --------------------------------------------------------------------------
# the verb
# --------------------------------------------------------------------------

class TestTheCli:
    def test_write_then_verify_round_trips(self, behind, tmp_path):
        target = tmp_path / "patch.json"
        done = cli("work", "understanding-patch", "--records-root", str(behind),
                   "--write", str(target))
        assert done.returncode == 0, done.stderr
        assert "待确认 1" in done.stdout
        assert str(target) in done.stdout
        done = cli("work", "understanding-patch", "--records-root", str(behind),
                   "--verify", str(target))
        assert done.returncode == 0, done.stderr
        assert "ok" in done.stdout

    def test_a_verify_that_finds_drift_exits_nonzero_with_the_reasons(
            self, behind, tmp_path):
        target = tmp_path / "patch.json"
        cli("work", "understanding-patch", "--records-root", str(behind),
            "--write", str(target))
        write_messages(behind, "people/x/oc_x", messages(12))
        done = cli("work", "understanding-patch", "--records-root", str(behind),
                   "--verify", str(target), "--json")
        assert done.returncode == 1
        payload = json.loads(done.stdout)
        assert payload["ok"] is False and payload["reasons"]

    def test_a_relative_records_root_is_refused(self):
        done = cli("work", "understanding-patch", "--records-root", "records")
        assert done.returncode == 1
        assert "must be absolute" in done.stderr

    def test_a_relative_written_destination_is_refused(self, behind, tmp_path):
        """From a directory the test owns — see the module-level test above."""
        done = cli("work", "understanding-patch", "--records-root", str(behind),
                   "--write", "patch.json", cwd=tmp_path)
        assert done.returncode == 1
        assert "not absolute" in done.stderr
        assert list(tmp_path.glob("patch.json")) == []

    def test_an_unreadable_decisions_file_is_refused_with_its_reason(self, behind, tmp_path):
        bad = tmp_path / "bad.jsonl"
        bad.write_text('{"decision": "CONFIRMED"}\n', encoding="utf-8")
        done = cli("work", "understanding-patch", "--records-root", str(behind),
                   "--decisions", str(bad))
        assert done.returncode == 1
        assert ":1:" in done.stderr

    def test_json_is_the_same_patch_the_module_builds(self, behind):
        done = cli("work", "understanding-patch", "--records-root", str(behind),
                   "--json")
        assert done.returncode == 0, done.stderr
        assert json.loads(done.stdout) == patch_of(behind)

    def test_it_takes_no_project_flag_because_the_layer_is_not_a_projects(self):
        done = cli("work", "understanding-patch", "--help")
        assert done.returncode == 0
        assert "--project" not in done.stdout
        assert "--records-root" in done.stdout and "--decisions" in done.stdout

    def test_it_loads_the_module_rather_than_shelling_out_to_it(self):
        source = (REPO_ROOT / "runtime" / "cli.py").read_text(encoding="utf-8")
        body = source.split("def cmd_work_understanding_patch(")[1].split("\ndef ")[0]
        assert "understanding_patch.py" in body
        assert "understanding_delta.py" in body and "capture_coverage.py" in body
        assert "subprocess" not in body


# --------------------------------------------------------------------------
# the live layer
# --------------------------------------------------------------------------

class TestAgainstTheRecordedLayer:
    def test_the_live_layer_produces_proposals_all_awaiting(self):
        patch = up.build(read(LIVE_RECORDS))
        assert patch["cards_read"] > 0
        assert patch["items"], "the live layer is behind its sources"
        assert patch["awaiting"] == len(patch["items"])
        assert patch["confirmed"] == 0 and patch["rejected"] == 0

    def test_every_live_proposal_carries_a_version_pair(self):
        for item in up.build(read(LIVE_RECORDS))["items"]:
            assert item["card"].startswith("information/understanding/")
            assert item["manifest"], item
            if item["review"] == "WHOLE_DOCUMENT":
                assert item["content_sha256"] or item["cited_digest"] is None
            else:
                assert item["source_max"] is not None, item
                assert item["applied"] is not None, item
                assert item["from_position"] <= item["to_position"], item

    def test_the_live_census_covers_every_card(self):
        patch = up.build(read(LIVE_RECORDS))
        assert sum(patch["states"].values()) == patch["cards_read"]
        assert sum(patch["no_item"].values()) + len({
            item["card"] for item in patch["items"]}) == patch["cards_read"]

    def test_the_live_patch_verifies_against_the_live_records(self, tmp_path):
        target = tmp_path / "live-patch.json"
        up.write(up.build(read(LIVE_RECORDS)), target)
        assert up.verify(read(LIVE_RECORDS), {}, None, target) == []
