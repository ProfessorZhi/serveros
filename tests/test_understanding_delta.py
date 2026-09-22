"""A card's claim to traceability, checked against the source it cites.

WHY THIS FILE EXISTS
--------------------
`records/information/understanding/README.md` requires every card to be traceable
to a raw source. Read on 2026-09-22, nothing measured that: `intake/workchain.py`
counts cards carrying a `## Sources` heading, which is a count of documents that
*wrote the rule down*, and `grep -rn "message_position" --include=*.py` over the
fabric tree returned nothing at all. So every property below is asserted here for
the first time. The six that are individually easy to get wrong each get a class:

  * two watermarks are not one. The card's reach is a position and the source's
    reach is a position, and a lag exists only when both are. `sync.msg_count` is
    a count and never a position, so it cannot produce a lag however tempting the
    subtraction.
  * a citation with no reach is not current. Seven of the fourteen live cards
    cite whole files; there is no comparison to make, and `UNMEASURABLE` is the
    answer rather than the comparison nobody made.
  * an edited message at a position the card already read leaves it stale while
    every position comparison says it is current. That is `STALE_IN_PLACE`.
  * no claim is emitted. This reader opens no message body, so it proposes a
    range; there is no `claim` key in the output, and its absence is asserted.
  * a glob does not cross a `/`. `people/*/messages.jsonl` is a card naming each
    person's chat, and expanding it into every linked document under every person
    would add rows the card never cited.
  * the reader writes nothing and reads no clock. Both are source-level claims
    about the module, checked against the module.

The live test at the end re-walks the real records root and asserts the report's
denominator equals the number of cards actually there, so a fixture that drifts
from the tree fails as a drift and not as an edit.
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
LIVE_RECORDS = Path("/data/home/vince/workspace/records")


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(
        "serveros_intake_" + name, REPO_ROOT / "intake" / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = _load("capture_coverage", "capture_coverage.py")
ud = _load("understanding_delta", "understanding_delta.py")


def cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, cwd=str(REPO_ROOT))


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

# Two coordinate systems, and a fixture that mixes them proves nothing.
# `CHAT` and `DOC` are how a CARD writes a path: relative to the records root,
# because that is what a reader of the card can open. `RAW` is how the capture
# tree is reached: `capture_coverage` keys its objects relative to
# `information/raw`, so the writers below take a path relative to `RAW`.
CHAT = "information/raw/feishu/chats/people/x/oc_x/messages.jsonl"
GROUP = "information/raw/feishu/chats/groups/g/oc_g/messages.jsonl"
DOC = "information/raw/feishu/chats/people/x/oc_x/links/d/content.md"
RAW = Path("information/raw/feishu/chats")


def chat_manifest(**over) -> dict:
    """A chat capture shaped like the real ones, with only the named fields changed."""
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
    """A records tree a test owns: an understanding layer and a capture tree."""
    records = tmp_path / "records"
    for relative in ("information/understanding/people",
                     "information/raw/feishu/chats"):
        (records / relative).mkdir(parents=True)
    return records


def write_capture(records: Path, relative: str, document) -> Path:
    """One capture object, at the place the collectors write manifests.

    `relative` is relative to `RAW` — `people/x/oc_x`, not the records-root path
    a card writes.
    """
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


def only_card(records: Path) -> dict:
    report = read(records)
    assert len(report["cards"]) == 1, report["summary"]
    return report["cards"][0]


# --------------------------------------------------------------------------
# two watermarks, never one
# --------------------------------------------------------------------------

class TestTwoWatermarksAreNotOne:
    def test_a_card_behind_its_source_reports_both_reaches(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(10))
        write_card(tree, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 3-4\n"))
        card = only_card(tree)
        row = card["measurements"][0]
        assert row["kind"] == "POSITION"
        assert (row["applied_low"], row["applied"]) == (3, 4)
        assert row["source_positions"]["max"] == 9
        assert row["lag"] == 5
        assert card["judged"]["state"] == "BEHIND"

    def test_a_card_at_the_newest_position_is_current(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(10))
        write_card(tree, "people", "x.md", heading_card(f"- `{CHAT}` message_position 0-9\n"))
        card = only_card(tree)
        assert card["measurements"][0]["lag"] == 0
        assert card["judged"]["state"] == "CURRENT"

    def test_a_count_is_not_a_position_and_forms_no_lag(self, tree):
        """The manifest's `msg_count` is 10 and the card cites no position.

        Subtracting a cited position from a count is the arithmetic that looks
        right here, and there is no cited position to subtract from. The row is
        `WHOLE_FILE`, carries the count the manifest holds, and carries no `lag`.
        """
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(10))
        write_card(tree, "people", "x.md", heading_card(f"- `{CHAT}`\n"))
        row = only_card(tree)["measurements"][0]
        assert row["kind"] == "WHOLE_FILE"
        assert row["source_count"] == 10
        assert "lag" not in row

    def test_the_manifest_count_and_the_positions_are_both_reported(self, tree):
        """Two independent facts about the same source, neither derived."""
        write_capture(tree, "people/x/oc_x",
                      chat_manifest(sync={**chat_manifest()["sync"],
                                          "msg_count": 10,
                                          "remote_unique_message_count": 9}))
        write_messages(tree, "people/x/oc_x", messages(10))
        write_card(tree, "people", "x.md", heading_card(f"- `{CHAT}` message_position 0-9\n"))
        row = only_card(tree)["measurements"][0]
        assert row["source_count"] == 10
        assert row["independent_count"] == 9
        assert row["source_positions"]["count"] == 10


class TestAnAbsenceIsNotAValue:
    def test_a_card_with_no_sources_marker_is_untraceable_not_current(self, tree):
        write_card(tree, "people", "x.md", "# 卡\n\n内容，没有任何出处。\n")
        card = only_card(tree)
        assert card["judged"]["state"] == "UNTRACEABLE"
        assert "nothing here can be checked" in card["judged"]["why"]

    def test_a_sources_marker_citing_nothing_is_untraceable(self, tree):
        write_card(tree, "people", "x.md",
                   "# 卡\n\n## Sources\n\n- 记忆，以及以往的经验。\n")
        card = only_card(tree)
        assert card["judged"]["state"] == "UNTRACEABLE"
        assert "cites no raw path" in card["judged"]["why"]

    def test_a_citation_with_no_reach_is_unmeasurable_not_current(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(10))
        write_card(tree, "people", "x.md", heading_card(f"- `{CHAT}`\n"))
        card = only_card(tree)
        assert card["judged"]["state"] == "UNMEASURABLE"
        assert "no reach to be behind" in card["judged"]["why"]

    @pytest.mark.parametrize("bad", ["", "nine", None])
    def test_a_position_that_is_not_an_integer_makes_the_reach_undecided(self, tree, bad):
        """Not smaller: `max` over the records that happened to have one would
        understate how far the source reaches by exactly what the reader missed."""
        rows = messages(10)
        rows[7]["message_position"] = bad
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", rows)
        write_card(tree, "people", "x.md", heading_card(f"- `{CHAT}` message_position 0-9\n"))
        card = only_card(tree)
        row = card["measurements"][0]
        assert row["source_positions"]["state"] == "POSITIONS_INCOMPLETE"
        assert row["kind"] == "WHOLE_FILE"
        assert "cannot be stated" in row["source_positions"]["why"]
        assert card["judged"]["state"] == "UNMEASURABLE"

    def test_an_unresolvable_citation_is_unresolved_not_current(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(4))
        write_card(tree, "people", "gone.md",
                   heading_card("- `information/raw/feishu/chats/people/absent/oc_a/"
                                "messages.jsonl` message_position 0-1\n"))
        card = only_card(tree)
        assert card["judged"]["state"] == "UNMEASURABLE"
        assert "did not capture" in card["measurements"][0]["unresolved_why"]


class TestAStaleCardIsNotACurrentOne:
    def test_an_edited_message_inside_the_applied_range_is_stale_in_place(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(10, edited=(2, 5)))
        write_card(tree, "people", "x.md", heading_card(f"- `{CHAT}` message_position 0-9\n"))
        card = only_card(tree)
        assert card["measurements"][0]["lag"] == 0
        assert card["measurements"][0]["revisions_in_range"] == {"edited": 2, "deleted": 0,
                                                                  "readable": True}
        assert card["judged"]["state"] == "STALE_IN_PLACE"

    def test_a_deleted_message_inside_the_applied_range_is_stale_in_place(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(10, deleted=(1,)))
        write_card(tree, "people", "x.md", heading_card(f"- `{CHAT}` message_position 0-9\n"))
        assert only_card(tree)["judged"]["state"] == "STALE_IN_PLACE"

    def test_a_revision_past_the_applied_range_is_not_a_in_place_staleness(self, tree):
        """The card is behind, and the edit is beyond what it read. Reporting the
        revision here would name a message the card never saw."""
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(10, edited=(8,)))
        write_card(tree, "people", "x.md", heading_card(f"- `{CHAT}` message_position 0-4\n"))
        card = only_card(tree)
        assert card["measurements"][0]["revisions_in_range"]["edited"] == 0
        assert card["judged"]["state"] == "BEHIND"

    def test_being_behind_beats_being_stale_in_place(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(10, edited=(2,)))
        write_card(tree, "people", "x.md", heading_card(f"- `{CHAT}` message_position 0-5\n"))
        card = only_card(tree)
        assert card["judged"]["state"] == "BEHIND"
        kinds = [row["kind"] for row in card["judged"]["review_ranges"]]
        assert kinds == ["BEYOND_APPLIED", "REVISITED_IN_PLACE"]

    def test_a_cited_digest_that_no_longer_matches_is_stale_in_place(self, tree):
        write_capture(tree, "people/x/oc_x/links/d",
                      document_manifest(content_sha256="b" * 64))
        write_card(tree, "people", "x.md", heading_card(f"- `{DOC}`（sha `beef1234`）\n"))
        card = only_card(tree)
        row = card["measurements"][0]
        assert row["kind"] == "DIGEST"
        assert row["cited_digest"] == "beef1234"
        assert row["content_sha256"] == "b" * 64
        assert row["lag"] == 1
        assert card["judged"]["state"] == "STALE_IN_PLACE"

    def test_a_replaced_document_proposes_a_range_with_no_positions(self, tree):
        """A state that implies work must propose something to look at.

        It used to contribute the state and no range, so the verdict read
        STALE_IN_PLACE beside an empty list — a card told it is stale with
        nothing to re-read. A document has no positions, so the range carries
        the two digests that made the comparison instead of inventing a span.
        """
        write_capture(tree, "people/x/oc_x/links/d",
                      document_manifest(content_sha256="b" * 64))
        write_card(tree, "people", "x.md", heading_card(f"- `{DOC}`（sha `beef1234`）\n"))
        ranges = only_card(tree)["judged"]["review_ranges"]
        assert [row["kind"] for row in ranges] == ["WHOLE_DOCUMENT"]
        assert ranges[0]["from_position"] is None
        assert ranges[0]["to_position"] is None
        assert ranges[0]["cited_digest"] == "beef1234"
        assert ranges[0]["content_sha256"] == "b" * 64

    def test_a_matching_digest_prefix_is_current(self, tree):
        write_capture(tree, "people/x/oc_x/links/d",
                      document_manifest(content_sha256="beef1234" + "0" * 56))
        write_card(tree, "people", "x.md", heading_card(f"- `{DOC}`（sha `beef1234`）\n"))
        card = only_card(tree)
        assert card["measurements"][0]["lag"] == 0
        assert card["judged"]["state"] == "CURRENT"


class TestNoClaimIsEmitted:
    def _keys(self, node) -> set[str]:
        found: set[str] = set()
        if isinstance(node, dict):
            for key, value in node.items():
                found.add(key)
                found |= self._keys(value)
        elif isinstance(node, list):
            for item in node:
                found |= self._keys(item)
        return found

    def test_no_field_anywhere_asserts_what_the_card_understood(self, tree):
        """Checked over the report's KEYS rather than its text.

        `"claim" not in json.dumps(...)` finds the phrase "the card claims
        traceability" in an `UNTRACEABLE` reason — prose about a card, not a
        field about one. A field named `claim` would be a field this reader has
        no producer for: it opens no message body, so anything it wrote there
        would be inferred from a count.
        """
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(10, edited=(1,)))
        write_card(tree, "people", "x.md", heading_card(f"- `{CHAT}` message_position 0-5\n"))
        keys = self._keys(read(tree))
        for forbidden in ("claim", "claims", "assertion", "asserted",
                          "user_confirmed", "confirmed", "understood", "conclusion"):
            assert forbidden not in keys, forbidden
        # And the ranges this module does emit are named for what they are:
        # a range to re-read, in the shapes `REVIEW_PRODUCED` declares.
        assert "review_ranges" in ud.VERDICT_PRODUCED
        assert {"from_position", "to_position", "kind"} <= set(ud.REVIEW_PRODUCED)
        assert {"kind", "from_position", "to_position", "review_ranges"} <= keys

    def test_what_is_emitted_is_a_range_and_says_so(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(10))
        write_card(tree, "people", "x.md", heading_card(f"- `{CHAT}` message_position 0-5\n"))
        ranges = only_card(tree)["judged"]["review_ranges"]
        assert [row["kind"] for row in ranges] == ["BEYOND_APPLIED"]
        assert (ranges[0]["from_position"], ranges[0]["to_position"]) == (6, 9)
        assert ranges[0]["card"] == "information/understanding/people/x.md"

    def test_no_measurement_carries_message_content(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(4))
        write_card(tree, "people", "x.md", heading_card(f"- `{CHAT}` message_position 0-3\n"))
        serialized = json.dumps(read(tree), ensure_ascii=False)
        assert '"content"' not in serialized


# --------------------------------------------------------------------------
# how a citation is read
# --------------------------------------------------------------------------

class TestTheCitationIsReadAsWritten:
    def test_a_position_clause_attaches_to_the_nearest_preceding_path(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(2))
        write_capture(tree, "groups/g/oc_g", chat_manifest())
        write_messages(tree, "groups/g/oc_g", messages(4))
        write_card(tree, "people", "x.md", heading_card(
            f"- `{CHAT}` message_position 0-1；`{GROUP}` message_position 0-3\n"))
        card = only_card(tree)
        assert [row["applied"] for row in card["measurements"]] == [1, 3]
        assert card["judged"]["state"] == "CURRENT"

    def test_several_ranges_in_one_clause_take_the_oldest_and_newest(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(20))
        write_card(tree, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 3-4、11-12\n"))
        row = only_card(tree)["measurements"][0]
        assert (row["applied_low"], row["applied"]) == (3, 12)
        assert row["lag"] == 7

    def test_positions_before_any_path_are_a_malformed_card_not_a_silent_drop(self, tree):
        write_card(tree, "people", "x.md",
                   heading_card("- message_position 0-9，见下面的引用\n"
                                f"- `{CHAT}`\n"))
        card = only_card(tree)
        assert card["state"] == "UNREADABLE"
        assert "before any backticked raw path" in card["why"]
        assert card["judged"]["state"] == "UNREADABLE"

    def test_a_bold_bullet_marker_is_read_as_a_sources_region(self, tree):
        """The people cards use this form and `workchain.py` counts the other one."""
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(4))
        write_card(tree, "people", "x.md",
                   f"# 人卡\n\n- **Sources**：\n  - `{CHAT}` message_position 0-3\n")
        card = only_card(tree)
        assert card["sources_marker"] == "bold-list"
        assert card["judged"]["state"] == "CURRENT"

    def test_a_region_ends_at_the_next_sentence_and_not_at_the_end_of_file(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(4))
        write_card(tree, "people", "x.md",
                   "# 卡\n\n## Sources\n\n- `" + CHAT + "` message_position 0-3\n\n"
                   "## 注\n\n后来的一段话，不该被读成出处。\n")
        card = only_card(tree)
        assert len(card["citations"]) == 1
        assert card["judged"]["state"] == "CURRENT"

    def test_the_last_marker_wins_over_a_mention_earlier_in_the_card(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(4))
        write_card(tree, "people", "x.md",
                   "# 卡\n\n## Sources 是什么\n\n说明文字。\n\n## Sources\n\n"
                   f"- `{CHAT}` message_position 0-3\n")
        card = only_card(tree)
        assert len(card["citations"]) == 1
        assert card["judged"]["state"] == "CURRENT"


class TestTheGlobDoesNotCrossASeparator:
    def test_a_person_glob_does_not_reach_into_their_linked_documents(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(4))
        write_capture(tree, "people/x/oc_x/links/d", document_manifest())
        write_card(tree, "people", "x.md",
                   heading_card("- `information/raw/feishu/chats/people/*/messages.jsonl`\n"))
        row = only_card(tree)["measurements"][0]
        assert row["kind"] == "UNRESOLVED"
        assert "does not cross a `/`" in row["unresolved_why"]
        assert "captured" in row["unresolved_why"]

    def test_a_glob_at_the_right_depth_measures_every_member(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(4))
        write_card(tree, "people", "x.md",
                   heading_card("- `information/raw/feishu/chats/people/x/*/messages.jsonl`\n"))
        row = only_card(tree)["measurements"][0]
        assert row["kind"] == "WHOLE_FILE"
        assert row["via_glob"] == "feishu/chats/people/x/oc_x/manifest.json"

    def test_an_expanded_glob_row_names_the_member_it_came_from(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(4))
        write_card(tree, "people", "x.md",
                   heading_card("- `information/raw/feishu/chats/people/*/*/messages.jsonl`\n"))
        card = only_card(tree)
        assert len(card["measurements"]) == 1
        assert card["measurements"][0]["source"].endswith("people/*/*/messages.jsonl")


class TestTheCoordinateSystemsStayApart:
    def test_a_citation_outside_the_capture_tree_says_so(self, tree):
        write_card(tree, "people", "x.md",
                   heading_card("- `records/information/raw/tests/thing.jsonl`\n"))
        row = only_card(tree)["measurements"][0]
        assert row["kind"] == "UNRESOLVED"
        assert "outside the capture tree" in row["unresolved_why"]
        # The two failures are different and the reason distinguishes them.
        assert "did not capture" not in row["unresolved_why"]

    def test_a_card_that_mixes_a_measured_and_an_unmeasured_citation_is_partial(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(4))
        write_capture(tree, "groups/g/oc_g", chat_manifest())
        write_card(tree, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-3\n"
                                f"- `{GROUP}`\n"))
        card = only_card(tree)
        verdict = card["judged"]
        assert verdict["state"] == "CURRENT"
        assert (verdict["measured"], verdict["unmeasured"], verdict["partial"]) == (1, 1, True)
        assert "were not compared" in verdict["why"]

    def test_a_citation_that_resolves_to_nothing_also_makes_the_card_partial(self, tree):
        """The narrower door, closed.

        A card whose one citation is current and whose other names a source this
        node never captured is not a checked card. Counting only the citations
        that named no reach would report this one as fully checked by leaving the
        unresolved one out of `partial` — and `CURRENT` over a citation that does
        not exist is the strongest form of the laundering `partial` is for.
        """
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(4))
        write_card(tree, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-3\n"
                                f"- `{GROUP}`\n"))
        card = only_card(tree)
        verdict = card["judged"]
        assert verdict["state"] == "CURRENT"
        assert (verdict["measured"], verdict["unmeasured"], verdict["unresolved"],
                verdict["partial"]) == (1, 0, 1, True)
        assert "resolve to no captured source" in verdict["why"]


# --------------------------------------------------------------------------
# the guard, the denominator, and what the reader does not do
# --------------------------------------------------------------------------

class TestEveryFieldHasAProducer:
    def test_a_measurement_key_with_no_producer_is_a_refusal(self):
        with pytest.raises(ud.Refused) as caught:
            ud.no_unsourced({"kind": "POSITION", "smuggled": 1},
                            ud.MEASUREMENT_PRODUCED, "measurement")
        assert "`measurement.smuggled`" in str(caught.value)
        assert "add the producer or drop the field" in str(caught.value)

    def test_a_verdict_key_with_no_producer_is_a_refusal(self):
        with pytest.raises(ud.Refused) as caught:
            ud.no_unsourced({"state": "CURRENT", "guess": True},
                            ud.VERDICT_PRODUCED, "verdict")
        assert "`verdict.guess`" in str(caught.value)

    def test_every_state_is_in_the_declared_set(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(4))
        write_card(tree, "people", "x.md", heading_card(f"- `{CHAT}` message_position 0-3\n"))
        for card in read(tree)["cards"]:
            assert card["judged"]["state"] in ud.CARD_STATES

    def test_a_reader_defect_is_not_reported_as_an_unreadable_card(self, monkeypatch, tree):
        """The two failures must not be collapsed.

        A `Refused` raised by this module's own guard is a defect HERE, and a
        `Malformed` is a fact about the card. `read_card` catches the second and
        must let the first through: catching `Refused` would report the author's
        card as unreadable because the reader had a bug, which is the same
        misattribution one level up that `capture_coverage` refuses.
        """
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(4))
        write_card(tree, "people", "x.md", heading_card(f"- `{CHAT}` message_position 0-1\n"))

        def malformed(_region):
            raise ud.Malformed("this card is not traceable as written")

        def refused(_region):
            raise ud.Refused("the reader emitted a key no producer names")

        monkeypatch.setattr(ud, "parse_citations", malformed)
        card = read(tree)["cards"][0]
        assert card["state"] == "UNREADABLE"
        assert "not traceable as written" in card["why"]

        monkeypatch.setattr(ud, "parse_citations", refused)
        with pytest.raises(ud.Refused) as caught:
            read(tree)
        assert "no producer names" in str(caught.value)

    def test_the_denominator_survives_a_bad_card(self, tree):
        write_card(tree, "people", "broken.md",
                   heading_card("- message_position 1-2，没有出处\n"))
        write_card(tree, "people", "fine.md", "# 卡\n\n## Sources\n\n- 无\n")
        report = read(tree)
        assert report["summary"]["denominator"] == 2
        assert report["summary"]["by_state"] == {"UNREADABLE": 1, "UNTRACEABLE": 1}


class TestTheReaderHoldsNoClockAndWritesNothing:
    """Source-level claims about the module, checked against its SYNTAX.

    Not against its text. The module's own docstring says it holds no clock
    ("there is no `import time`"), so a substring search over the source finds
    the sentence that promises the property and fails on it — a gate that can
    see a literal but not its context, which is the failure
    `docs/CC-EXECUTION-LEDGER.md` §5.0 records three times. `ast` reads the
    program instead of the prose.
    """

    def _tree(self):
        import ast
        source = (REPO_ROOT / "intake" / "understanding_delta.py").read_text(encoding="utf-8")
        return ast.parse(source)

    def test_the_module_imports_no_clock(self):
        import ast
        imported = set()
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "time" not in imported
        assert "datetime" not in imported

    def test_the_module_calls_no_clock(self):
        import ast
        called = {node.func.attr for node in ast.walk(self._tree())
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        for reader in ("now", "utcnow", "today", "monotonic", "time", "strftime"):
            assert reader not in called, reader

    def test_the_module_opens_the_source_file_for_reading_only(self):
        import ast
        opens = []
        for node in ast.walk(self._tree()):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            name = node.func.attr
            if name not in ("open", "write_text", "write_bytes", "mkdir", "unlink",
                            "rename", "rmtree", "replace", "chmod", "touch"):
                continue
            modes = [keyword.value.value for keyword in node.keywords
                     if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant)]
            modes += [arg.value for arg in node.args
                      if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
            opens.append((name, modes))
        assert opens, "the reader must open the messages file"
        for name, modes in opens:
            assert name == "open", f"`{name}` would write"
            assert not modes, f"a mode was passed to `open`: {modes}"

    def test_every_timestamp_it_reports_came_out_of_a_manifest(self):
        assert "`sync.latest_create_time` from the manifest" == \
            ud.MEASUREMENT_PRODUCED["latest_create_time"]
        assert "`sync.last_message_id` from the manifest" == \
            ud.MEASUREMENT_PRODUCED["last_message_id"]


# --------------------------------------------------------------------------
# the verb
# --------------------------------------------------------------------------

class TestTheCli:
    def test_it_renders_the_live_tree_and_exits_zero(self):
        done = cli("work", "understanding")
        assert done.returncode == 0, done.stderr
        assert "卡" in done.stdout
        assert "需要往下读" in done.stdout or "已读区间被改动过" in done.stdout

    def test_a_relative_records_root_is_refused(self):
        done = cli("work", "understanding", "--records-root", "records")
        assert done.returncode == 1
        assert "must be absolute" in done.stderr
        assert "nobody else can reproduce" in done.stderr

    def test_an_absent_records_root_refuses_with_a_reason(self, tmp_path):
        done = cli("work", "understanding", "--records-root", str(tmp_path))
        assert done.returncode == 1
        assert "not a directory" in done.stderr
        assert "not an empty one" in done.stderr

    def test_json_is_the_same_report_the_module_produces(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(6))
        write_card(tree, "people", "x.md", heading_card(f"- `{CHAT}` message_position 0-2\n"))
        done = cli("work", "understanding", "--records-root", str(tree), "--json")
        assert done.returncode == 0, done.stderr
        payload = json.loads(done.stdout)
        assert payload["schema"] == ud.SCHEMA
        assert payload["summary"] == read(tree)["summary"]
        assert payload["cards"][0]["judged"]["state"] == "BEHIND"

    def test_it_takes_no_project_flag_because_the_layer_is_not_a_projects(self):
        done = cli("work", "understanding", "--help")
        assert done.returncode == 0
        assert "--project" not in done.stdout
        assert "--records-root" in done.stdout

    def test_the_partial_line_names_both_kinds_of_unchecked_citation(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(4))
        write_capture(tree, "groups/g/oc_g", chat_manifest())
        write_card(tree, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-3\n"
                                f"- `{GROUP}`\n"
                                f"- `information/raw/feishu/chats/absent/oc_a/"
                                f"messages.jsonl`\n"))
        done = cli("work", "understanding", "--records-root", str(tree))
        assert done.returncode == 0, done.stderr
        assert "部分：1 条引文可比" in done.stdout
        assert "1 条没有可比的位置" in done.stdout
        assert "1 条没有对应的源" in done.stdout

    def test_it_loads_the_module_rather_than_shelling_out_to_it(self):
        source = (REPO_ROOT / "runtime" / "cli.py").read_text(encoding="utf-8")
        body = source.split("def cmd_work_understanding(")[1].split("\ndef ")[0]
        assert "understanding_delta.py" in body and "capture_coverage.py" in body
        assert "subprocess" not in body
        # And the module has no `main()` for a verb to reach for instead.
        assert not hasattr(ud, "main")


# --------------------------------------------------------------------------
# the live layer
# --------------------------------------------------------------------------

class TestAgainstTheRecordedLayer:
    def test_the_denominator_is_the_number_of_cards_actually_there(self):
        report = read(LIVE_RECORDS)
        root = LIVE_RECORDS / "information" / "understanding"
        present = [path for path in root.rglob("*.md") if path.name != "README.md"]
        assert report["summary"]["denominator"] == len(present)
        assert report["summary"]["denominator"] > 0

    def test_every_live_card_is_in_a_declared_state(self):
        for card in read(LIVE_RECORDS)["cards"]:
            assert card["judged"]["state"] in ud.CARD_STATES

    def test_the_layer_states_are_counted_and_never_folded_into_one(self):
        summary = read(LIVE_RECORDS)["summary"]
        assert sum(summary["by_state"].values()) == summary["denominator"]
        assert "verdict" not in summary
        for scope, bucket in summary["by_scope"].items():
            assert sum(bucket.values()) > 0, scope

    def test_the_two_marker_forms_are_both_present_and_named(self):
        """The people cards use a bold bullet and the projects cards a heading.

        `workchain.py` counts only the heading form, which is why its
        traceability count is lower than the number of cards that cite sources.
        This is a difference between the scopes and not a defect in either.
        """
        markers = read(LIVE_RECORDS)["summary"]["by_sources_marker"]
        assert markers.get("heading", 0) > 0
        assert markers.get("bold-list", 0) > 0
