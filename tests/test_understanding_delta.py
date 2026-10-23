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

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "runtime" / "cli.py"
from live_checkouts import readable_dir  # noqa: E402

LIVE_RECORDS = Path("/data/home/vince/workspace/records")


def _cli_records_root() -> Path:
    """The records root `serverosctl work understanding` reads by default."""
    spec = importlib.util.spec_from_file_location(
        "serveros_runtime_paths", REPO_ROOT / "runtime" / "paths.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return Path(module.Paths().root("workspace", unbound_ok=True)) / "records"


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


DOC_BODY = "body\n"


def document_manifest(body: str = DOC_BODY, **over) -> dict:
    """A document capture shaped like the real ones, with one body behind it.

    `content_sha256` is computed from `body` rather than written by hand. It used
    to be a literal (`"a" * 64`) with no `content.md` anywhere, i.e. a manifest
    whose recorded digest describes no bytes at all — a shape the tree does not
    actually have, but one that made every test here assume the defect while
    claiming to be about which version a CARD names. `write_document` now writes
    the file this digest names, so the default fixture is a manifest whose record
    describes its file, and a test that wants the two apart says so:
    `TestAManifestsRecordIsNotItsFilesDigest` does, and the four live-wrong
    answers it separates are quoted in its own docstring.
    """
    document = {
        "source_message_id": "om_x", "url": "https://example.invalid/d",
        "type": "docx", "title": "T", "fetched_at": "2026-09-07",
        "content_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "redaction_applied": False,
    }
    document.update(over)
    return document


def write_document(records: Path, relative: str, body: str = DOC_BODY,
                   **over) -> dict:
    """One document capture AND the body its digest names. Returns the manifest."""
    document = document_manifest(body, **over)
    where = records / RAW / relative
    where.mkdir(parents=True, exist_ok=True)
    (where / "content.md").write_text(body, encoding="utf-8")
    (where / "manifest.json").write_text(
        json.dumps(document, ensure_ascii=False), encoding="utf-8")
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
        document = write_document(tree, "people/x/oc_x/links/d",
                                  prev_content_sha256="beef1234" + "0" * 56)
        write_card(tree, "people", "x.md", heading_card(f"- `{DOC}`（sha `beef1234`）\n"))
        card = only_card(tree)
        row = card["measurements"][0]
        assert row["kind"] == "DIGEST"
        assert row["cited_digest"] == "beef1234"
        assert row["content_sha256"] == document["content_sha256"]
        assert row["lag"] == 1
        assert card["judged"]["state"] == "STALE_IN_PLACE"

    def test_a_replaced_document_proposes_a_range_with_no_positions(self, tree):
        """A state that implies work must propose something to look at.

        It used to contribute the state and no range, so the verdict read
        STALE_IN_PLACE beside an empty list — a card told it is stale with
        nothing to re-read. A document has no positions, so the range carries
        the digests that made the comparison instead of inventing a span: the
        one the card cites and the ones the manifest records, because "the
        document was re-published" is one of three standings and the range is
        read by a person deciding what to do about it.
        """
        document = write_document(tree, "people/x/oc_x/links/d",
                                  prev_content_sha256="beef1234" + "0" * 56)
        write_card(tree, "people", "x.md", heading_card(f"- `{DOC}`（sha `beef1234`）\n"))
        ranges = only_card(tree)["judged"]["review_ranges"]
        assert [row["kind"] for row in ranges] == ["WHOLE_DOCUMENT"]
        assert ranges[0]["from_position"] is None
        assert ranges[0]["to_position"] is None
        assert ranges[0]["cited_digest"] == "beef1234"
        assert ranges[0]["content_sha256"] == document["content_sha256"]
        assert ranges[0]["prev_content_sha256"] == "beef1234" + "0" * 56

    def test_a_matching_digest_prefix_is_current(self, tree):
        document = write_document(tree, "people/x/oc_x/links/d",
                                  prev_content_sha256="c" * 64)
        write_card(tree, "people", "x.md", heading_card(
            f"- `{DOC}`（sha `{document['content_sha256'][:8]}`）\n"))
        card = only_card(tree)
        assert card["measurements"][0]["lag"] == 0
        assert card["judged"]["state"] == "CURRENT"
        # No comparison was made against a predecessor, so no standing is
        # reported: an absent key is the answer, and a `CURRENT` row carrying a
        # standing would invite a reader to treat the comparison as having
        # happened.
        assert "cited_digest_standing" not in card["measurements"][0]


class TestACitedDigestStandsSomewhere:
    """A mismatch is not a version change, and the two are repaired differently.

    `lag` says the card is not at the version the manifest records. It does not
    say that the card ever read THIS document, and the reader used to answer as
    though it did: on the live layer a card citing `b9fe2535` was told the
    document had changed from `b9fe2535` to `7a586f53` — a digest appearing in
    none of that document's twelve committed versions, while the manifest's own
    predecessor was `13977522`. So the sentence named a version that never
    existed, which is this module's recurring defect: a fact about the reader
    (its comparison failed) reported as a fact about the world (a transition
    happened).

    A manifest keeps exactly two digests, so the reader can decide WHICH of
    three standings a citation is in, and it cannot decide more than that: a
    card two versions behind and a card quoting a digest that was never this
    document's measure the same here. The window says so by naming the two
    versions it compared rather than asserting a transition.
    """

    def test_a_citation_of_the_recorded_predecessor_is_a_measured_transition(self, tree):
        document = write_document(tree, "people/x/oc_x/links/d",
                                  prev_content_sha256="beef1234" + "0" * 56)
        write_card(tree, "people", "x.md", heading_card(f"- `{DOC}`（sha `beef1234`）\n"))
        card = only_card(tree)
        row = card["measurements"][0]
        assert row["content_sha256"] == document["content_sha256"]
        assert row["lag"] == 1
        assert row["cited_digest_standing"] == "CITED_IS_PREVIOUS_VERSION"
        assert row["prev_content_sha256"] == "beef1234" + "0" * 56
        rng = card["judged"]["review_ranges"][0]
        assert rng["kind"] == "WHOLE_DOCUMENT"
        assert rng["cited_digest_standing"] == "CITED_IS_PREVIOUS_VERSION"
        assert rng["prev_content_sha256"] == "beef1234" + "0" * 56

    def test_a_citation_that_is_neither_version_is_named_and_not_called_a_change(self, tree):
        document = write_document(tree, "people/x/oc_x/links/d",
                                  prev_content_sha256="c" * 64)
        write_card(tree, "people", "x.md", heading_card(f"- `{DOC}`（sha `beef1234`）\n"))
        card = only_card(tree)
        row = card["measurements"][0]
        assert row["content_sha256"] == document["content_sha256"]
        assert row["lag"] == 1
        assert row["cited_digest_standing"] == "CITED_IS_NEITHER_RECORDED_VERSION"
        assert row["prev_content_sha256"] == "c" * 64
        rng = card["judged"]["review_ranges"][0]
        assert rng["kind"] == "WHOLE_DOCUMENT"
        assert rng["cited_digest_standing"] == "CITED_IS_NEITHER_RECORDED_VERSION"
        assert rng["prev_content_sha256"] == "c" * 64
        # The state is the one it has always been; the reason is not. A
        # sentence saying the digest "no longer" matches would claim the card
        # once held the manifest's version, and this is the case where it
        # provably did not.
        assert card["judged"]["state"] == "STALE_IN_PLACE"
        assert "does not match" in card["judged"]["why"]
        assert "no longer" not in card["judged"]["why"]
        assert "neither the version the manifest records now" in card["judged"]["why"]

    def test_a_manifest_with_no_predecessor_cannot_support_a_transition(self, tree):
        write_document(tree, "people/x/oc_x/links/d")
        write_card(tree, "people", "x.md", heading_card(f"- `{DOC}`（sha `beef1234`）\n"))
        card = only_card(tree)
        row = card["measurements"][0]
        assert row["lag"] == 1
        assert row["cited_digest_standing"] == "NO_PREVIOUS_VERSION_RECORDED"
        # Present exactly when the manifest records a predecessor, at both
        # layers: a key that was always there would make "no previous version
        # was recorded" and "the previous version is null" the same reading, and
        # only the first of those is this reader's situation.
        assert "prev_content_sha256" not in row
        rng = card["judged"]["review_ranges"][0]
        assert rng["kind"] == "WHOLE_DOCUMENT"
        assert rng["cited_digest_standing"] == "NO_PREVIOUS_VERSION_RECORDED"
        assert "prev_content_sha256" not in rng
        # An unrecorded predecessor is not evidence that the citation is
        # unplaceable — it is the absence of a comparison — so it is not
        # counted in the sentence above.
        assert "neither the version the manifest records now" not in card["judged"]["why"]

    def test_the_reason_counts_only_the_citations_that_are_unplaceable(self, tree):
        """Two lagging documents, and only one of them is a proven straddle.

        The count is what a person triages on, so it has to be the number of
        citations the reader can show never named this document's version — not
        the number of citations that failed to match, which is the number it
        already had. `d` is a real version change (its citation IS the recorded
        predecessor); `e` is a citation of `cafe0000`, which is neither of the
        two versions that manifest keeps.
        """
        other = "information/raw/feishu/chats/people/x/oc_x/links/e/content.md"
        write_document(tree, "people/x/oc_x/links/d",
                       prev_content_sha256="beef1234" + "0" * 56)
        write_document(tree, "people/x/oc_x/links/e",
                       prev_content_sha256="e" * 64)
        write_card(tree, "people", "x.md",
                   heading_card(f"- `{DOC}`（sha `beef1234`）\n"
                                f"- `{other}`（sha `cafe0000`）\n"))
        verdict = only_card(tree)["judged"]
        assert verdict["state"] == "STALE_IN_PLACE"
        assert len(verdict["review_ranges"]) == 2
        assert "1 of those cite a digest that is neither" in verdict["why"]


class TestAManifestsRecordIsNotItsFilesDigest:
    """THE PRIOR QUESTION: does the record describe the bytes beside it?

    `content_identity` for a document range is the digest the manifest
    RECORDED, and until the file that record names is hashed, that value is a
    fact about the capture's own record. On this node it frequently is not the
    file's digest: of the twenty-one link manifests under `information/raw`,
    fourteen record a 64-hex value that is not what `sha256sum content.md`
    prints and three record a twelve-character prefix of it. Four wrong answers
    came out of not
    asking, all four measured on the live tree and reproduced against the
    installed CLI:

      * a card quoting the file's own digest was blamed for naming neither the
        version the manifest records nor its predecessor, and the range was
        pinned to a digest no bytes on this node have;
      * the same for a card quoting a short record that matches nothing;
      * a card agreeing with a twelve-character record to twelve of sixteen
        characters was called a mismatch — the shorter instrument's resolution
        reported as a fact about the document;
      * an uncomparable digest was counted in `measured`, so a card whose only
        citation had never been checked came out `CURRENT` with
        `partial: False`, which is the laundering `partial` exists to prevent.

    Answers rather than a verdict, because "I hashed the file and this record
    does not describe it" and "there is no file here to hash" are answers about
    different parties, and a manifest is not to be blamed for a reader that was
    not given the body's location.

    And the second answer — "it hashes to something else" — is itself two
    answers, which this class learned the hard way. The fourteen live manifests
    are NOT records that describe no bytes: each is the digest of exactly the
    body beside it, taken before the collector appended the newline it writes
    (`records`' `information/raw/expand_feishu_doc.py` says so in `render_doc`'s
    own docstring — before 2026-09-14 `main()` wrote `redacted + "\n"` and
    hashed `sha256(redacted)` — and names fifteen manifests written that way).
    A reader that stopped at `sha256(content.md) != recorded` and called it "the
    record describes other bytes" would report its own failed comparison as a
    fact about the document — the same defect this module keeps committing,
    committed by the fix for the previous instance of it.
    `test_a_record_of_the_content_before_the_writers_newline_is_this_file` is
    that second hash; nothing on the live tree is in the `OTHER_BYTES` class at
    all, so the guard below is exercised by the fixtures here rather than by the
    tree.
    """

    #: A 64-hex record that describes no bytes the fixture can produce, under
    #: EITHER of the collector's conventions. Deliberately not a live value: the
    #: tree has no manifest in this class, and a fixture that quoted one of the
    #: fourteen would be asserting the very defect this class now separates out.
    OTHER_BYTES = "deadbeef" + "0" * 56

    def test_a_record_that_describes_other_bytes_measures_nothing(self, tree):
        write_document(tree, "people/x/oc_x/links/d",
                       content_sha256=self.OTHER_BYTES)
        true_digest = hashlib.sha256(DOC_BODY.encode("utf-8")).hexdigest()
        write_card(tree, "people", "x.md", heading_card(
            f"- `{DOC}`（sha `{true_digest[:8]}`）\n"))
        card = only_card(tree)
        row = card["measurements"][0]
        assert row["kind"] == "DIGEST"
        assert row["cited_digest"] == true_digest[:8]
        # Copied out, not compared with: who recorded what is still readable.
        assert row["content_sha256"] == self.OTHER_BYTES
        assert row["recorded_digest_standing"] == "RECORDED_IS_NOT_THIS_FILES_DIGEST"
        assert self.OTHER_BYTES[:12] in row["digest_why"]
        # BOTH of the hashes this reader took, because the sentence it replaces
        # named only one and then concluded about the document. `DOC_BODY` ends
        # in a newline, as every collector-written body does, so this row is in
        # the class where the second hash had to be taken and still did not
        # match.
        assert true_digest[:12] in row["digest_why"]
        assert hashlib.sha256(DOC_BODY[:-1].encode("utf-8")).hexdigest()[:12] \
            in row["digest_why"]
        assert "lag" not in row and "cited_digest_standing" not in row
        # And the consequence: nothing on this card was compared.
        verdict = card["judged"]
        assert verdict["state"] == "UNMEASURABLE"
        assert verdict["measured"] == 0
        assert verdict["uncheckable"] == 1
        assert verdict["partial"] is False
        assert verdict["review_ranges"] == []
        assert "could not be compared with" in verdict["why"]

    def test_a_record_of_the_content_before_the_writers_newline_is_this_file(self, tree):
        """The live shape of fourteen manifests: the same file, hashed one byte early.

        `DOC_BODY` is `"body\\n"` and the collector writes it as it stands, so a
        record of `sha256(b"body")` is exactly what `render_doc` produced before
        2026-09-14 — a record of THIS document, not a record of other bytes. The
        card cites what the manifest of that era recorded, which is the live
        shape: `litellm.md` cites `b9fe2535`, the value recorded for that
        snapshot under the same convention.

        Measured, not closed. The record is this document's version pin, so the
        row keeps its comparison — and a row that refused to make it would
        report "I could not hash this" as "the document cannot be read", which
        is the same defect one hash earlier.
        """
        written = hashlib.sha256(DOC_BODY.encode("utf-8")).hexdigest()
        content = hashlib.sha256(b"body").hexdigest()
        assert DOC_BODY.endswith("\n") and written != content
        write_document(tree, "people/x/oc_x/links/d", content_sha256=content)
        write_card(tree, "people", "x.md", heading_card(
            f"- `{DOC}`（sha `{content}`）\n"))
        card = only_card(tree)
        row = card["measurements"][0]
        assert row["recorded_digest_standing"] == \
            "RECORDED_IS_THIS_FILES_CONTENT_DIGEST"
        # Comparable: no reason-to-refuse, and the comparison is on the row.
        assert "digest_why" not in row
        assert row["lag"] == 0
        # And the pin is still the recorded value, because the record is of this
        # document — the whole reason this standing is not the refusal.
        assert row["content_sha256"] == content
        verdict = card["judged"]
        assert verdict["state"] == "CURRENT"
        assert verdict["measured"] == 1
        assert verdict["uncheckable"] == 0
        assert verdict["partial"] is False

    def test_the_second_hash_is_only_taken_where_a_writer_added_a_newline(self, tree):
        """A file with no trailing newline has no "content" to hash separately.

        Chopping the last byte off a body that never had one hashes a byte
        string no writer produced, so a record that happened to equal it would be
        read as this file's — a match neither of them can show. The last byte of
        a file without a newline is content, and dropping it is the same
        mistake as dropping the newline when there is no newline.
        """
        body = "no newline at all"
        chopped = hashlib.sha256(body[:-1].encode("utf-8")).hexdigest()
        assert not body.endswith("\n") and chopped != hashlib.sha256(
            body.encode("utf-8")).hexdigest()
        write_document(tree, "people/x/oc_x/links/d", body, content_sha256=chopped)
        write_card(tree, "people", "x.md", heading_card(
            f"- `{DOC}`（sha `{chopped[:8]}`）\n"))
        row = only_card(tree)["measurements"][0]
        assert row["recorded_digest_standing"] == "RECORDED_IS_NOT_THIS_FILES_DIGEST"
        assert "lag" not in row
        assert only_card(tree)["judged"]["state"] == "UNMEASURABLE"

    def test_the_record_that_lies_is_never_handed_out_as_a_range_identity(self, tree):
        """The pin is the whole point: a range must not confirm a revision no file has.

        The card here quotes the manifest's OWN value, so before the fix the
        comparison agreed, the card came out `CURRENT`, and a range went out
        pinned to a digest that describes nothing on this node — a confirmation
        of a revision nobody can read. Asserted over the serialised report,
        because a range carrying the value is what a consumer acts on.
        """
        write_document(tree, "people/x/oc_x/links/d",
                       content_sha256=self.OTHER_BYTES)
        write_card(tree, "people", "x.md", heading_card(
            f"- `{DOC}`（sha `{self.OTHER_BYTES}`）\n"))
        card = only_card(tree)
        assert card["judged"]["review_ranges"] == []
        assert card["judged"]["state"] == "UNMEASURABLE"
        assert f'"content_identity": "{self.OTHER_BYTES}"' not in json.dumps(
            card, ensure_ascii=False)

    def test_a_short_record_is_compared_at_its_own_resolution(self, tree):
        """Twelve characters that ARE this file's prefix are a weaker instrument.

        The card cites more characters than the manifest kept. Both readings
        agree on every character either of them has, and a mismatch reported
        here would be the instrument's resolution said as a fact about the
        document — which is what the live layer said about three manifests.
        """
        document = write_document(tree, "people/x/oc_x/links/d")
        full = document["content_sha256"]
        write_document(tree, "people/x/oc_x/links/d",
                       content_sha256=full[:12])
        for cited in (full[:8], full[:16], full):
            write_card(tree, "people", "x.md", heading_card(
                f"- `{DOC}`（sha `{cited}`）\n"))
            card = only_card(tree)
            row = card["measurements"][0]
            assert row["lag"] == 0, cited
            assert "digest_why" not in row, cited
            assert row["recorded_digest_standing"] == "RECORDED_IS_THIS_FILES_DIGEST"
            assert card["judged"]["state"] == "CURRENT"

    def test_a_short_record_that_is_not_the_files_prefix_is_the_defect_too(self, tree):
        """Short and wrong is not short and right. Only the file's bytes separate them.

        `capture_coverage` flags a short digest as INCOMPLETE and calls a
        64-character one COMPLETE, so the two classes this reader has to keep
        apart — a record at a coarser resolution and a record describing other
        bytes — are told apart by hashing the file and by nothing else.
        """
        write_document(tree, "people/x/oc_x/links/d",
                       content_sha256="4657fe883c6e")
        true_digest = hashlib.sha256(DOC_BODY.encode("utf-8")).hexdigest()
        assert not true_digest.startswith("4657fe883c6e")
        write_card(tree, "people", "x.md", heading_card(
            f"- `{DOC}`（sha `{true_digest[:8]}`）\n"))
        card = only_card(tree)
        row = card["measurements"][0]
        assert row["recorded_digest_standing"] == "RECORDED_IS_NOT_THIS_FILES_DIGEST"
        assert "4657fe883c6e" in row["digest_why"]
        assert card["judged"]["state"] == "UNMEASURABLE"
        assert card["judged"]["review_ranges"] == []

    def test_no_file_beside_the_record_is_could_not_check_not_a_verdict(self, tree):
        """The third answer, and the one that must not blame the manifest.

        A manifest with no `content.md` beside it is a capture this reader
        cannot complete. It is not evidence that the record is wrong, so the
        standing is the "not checked" one and the reason names the absence
        rather than asserting a disagreement.
        """
        write_capture(tree, "people/x/oc_x/links/d",
                      document_manifest(content_sha256=self.OTHER_BYTES))
        write_card(tree, "people", "x.md", heading_card(f"- `{DOC}`（sha `beef1234`）\n"))
        row = only_card(tree)["measurements"][0]
        assert row["recorded_digest_standing"] == \
            "RECORDED_WAS_NOT_CHECKED_AGAINST_THE_FILE"
        assert "no readable document body sits beside it" in row["digest_why"]
        assert only_card(tree)["judged"]["state"] == "UNMEASURABLE"

    def test_a_web_capture_is_compared_but_says_what_it_compared_with(self, tree):
        """A web capture's body is a verification copy under an asset root.

        The reader is not given that root and must not go looking for it, so it
        cannot hash the body — but the body exists and the record is a claim
        about it, so the comparison is made and the standing travels beside it.
        Treating this like the file that is absent (no comparison) would disable
        every web capture; treating it like agreement with a body check would
        report a comparison this reader never made.
        """
        # Written directly rather than through `write_capture`, whose RAW is the
        # feishu capture root: a page capture lives under `information/raw/web/`,
        # and putting it under the chat tree to reuse a helper would make the
        # fixture's layout a claim about where pages are kept.
        where = tree / "information" / "raw" / "web" / "ref" / "x"
        where.mkdir(parents=True)
        (where / "manifest.json").write_text(json.dumps({
            "schema": "serveros/web-capture.v1",
            "source": {"provider": "web", "requested_url": "https://example.invalid/a",
                       "final_url": "https://example.invalid/a", "redirected": False},
            "fetch": {"at": "2026-09-23T18:31+08:00", "http_status": 200,
                      "sha256": self.OTHER_BYTES, "captured_by": "curl"},
            "identity_observed": {"title": "T"},
        }, ensure_ascii=False), encoding="utf-8")
        web = "information/raw/web/ref/x/content.md"
        write_card(tree, "people", "x.md", heading_card(f"- `{web}`（sha `beef1234`）\n"))
        card = only_card(tree)
        row = card["measurements"][0]
        assert row["kind"] == "DIGEST"
        assert row["recorded_digest_standing"] == "RECORDED_IS_A_BODY_HELD_ELSEWHERE"
        # Compared, and the reason the comparison is not a body check is the
        # standing itself — which is why it is not a `digest_why`.
        assert "digest_why" not in row
        assert row["lag"] == 1
        assert row["cited_digest_standing"] == "NO_PREVIOUS_VERSION_RECORDED"
        assert card["judged"]["state"] == "STALE_IN_PLACE"
        assert card["judged"]["review_ranges"][0]["content_identity"] == \
            self.OTHER_BYTES

    def test_a_manifest_with_no_digest_is_not_a_comparison_either(self, tree):
        """The key present and null is not a version anybody can be behind."""
        write_document(tree, "people/x/oc_x/links/d", content_sha256=None)
        write_card(tree, "people", "x.md", heading_card(f"- `{DOC}`（sha `beef1234`）\n"))
        card = only_card(tree)
        row = card["measurements"][0]
        assert "recorded_digest_standing" not in row
        assert "no `content_sha256`" in row["digest_why"]
        assert "lag" not in row
        assert card["judged"]["state"] == "UNMEASURABLE"
        assert card["judged"]["measured"] == 0

    def test_a_current_card_still_names_the_citation_it_could_not_check(self, tree):
        """`CURRENT` is the verdict for the citations that were compared.

        The comparison that happened is real and is reported; the one that did
        not is reported beside it rather than left out, because a reader seeing
        「CURRENT」 alone would take the unchecked citation for a checked one.
        """
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(4))
        write_document(tree, "people/x/oc_x/links/d",
                       content_sha256=self.OTHER_BYTES)
        write_card(tree, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-3\n"
                                f"- `{DOC}`（sha `beef1234`）\n"))
        verdict = only_card(tree)["judged"]
        assert verdict["state"] == "CURRENT"
        assert verdict["measured"] == 1
        assert verdict["uncheckable"] == 1
        assert verdict["partial"] is True
        assert "1 further citation(s) quote a content digest the manifest they " \
               "cite could not be compared with" in verdict["why"]

    def test_a_lagging_card_mixed_with_an_uncheckable_one_is_partial(self, tree):
        """One comparison happened and one could not be made, and both are said.

        The state is the one the comparison supports and `partial` says it is
        not the whole card, with the uncheckable count carried out so a caller
        rendering a reason can name all three kinds of unchecked citation.
        """
        document = write_document(tree, "people/x/oc_x/links/d",
                                  prev_content_sha256="beef1234" + "0" * 56)
        other = "information/raw/feishu/chats/people/x/oc_x/links/e/content.md"
        write_document(tree, "people/x/oc_x/links/e",
                       content_sha256=self.OTHER_BYTES)
        write_card(tree, "people", "x.md",
                   heading_card(f"- `{DOC}`（sha `beef1234`）\n"
                                f"- `{other}`（sha `cafe0000`）\n"))
        verdict = only_card(tree)["judged"]
        assert verdict["state"] == "STALE_IN_PLACE"
        assert verdict["measured"] == 1
        assert verdict["uncheckable"] == 1
        assert verdict["partial"] is True
        # One range, and it is about the document that was compared: the
        # uncheckable citation contributes no range, so the other document's
        # record is nowhere in the pins.
        assert len(verdict["review_ranges"]) == 1
        assert verdict["review_ranges"][0]["content_identity"] == \
            document["content_sha256"]
        assert verdict["review_ranges"][0]["prev_content_sha256"] == \
            "beef1234" + "0" * 56


class TestAPositionIsNotAVersion:
    """The range says WHERE, and the digest says WHICH REVISION of it.

    Everything else in this module measures a place: a position, a span, a
    count. A place can be rewritten underneath a decision that was taken about
    it — a message edited with no flag set, a card rewritten at its own path —
    and a reader that reported only the place would hand the writer an identity
    that survives the rewrite. So the digest is computed here, over the records
    the span names, and the properties below are the ones the writer's whole
    "a confirmation covers a revision" rule rests on.
    """

    def test_the_digest_moves_when_the_content_does_and_the_place_does_not(
            self, tree):
        """The load-bearing pair: same span, same flags, different text.

        The `updated` flag is not set and no position changes, so every other
        number in this report is identical across the two reads. If the digest
        did not move here it would be a digest over the projection the flags
        already carry, and `STALE_IN_PLACE` would be the only thing standing
        between a rewrite and a decision taken before it.
        """
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(10))
        write_card(tree, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-4\n"))
        before = only_card(tree)["judged"]["review_ranges"][0]
        source_before = only_card(tree)["measurements"][0]

        rows = messages(10)
        rows[7]["content"] = "改过的一句话"
        write_messages(tree, "people/x/oc_x", rows)
        after = only_card(tree)["judged"]["review_ranges"][0]
        source_after = only_card(tree)["measurements"][0]

        assert before["content_identity"] != after["content_identity"]
        # The place is untouched, and the test says so: an assertion that only
        # saw the digest move would also pass if the span had moved with it.
        assert (before["from_position"], before["to_position"]) == \
            (after["from_position"], after["to_position"]) == (5, 9)
        assert source_before["source_positions"] == source_after["source_positions"]
        assert before["kind"] == after["kind"] == "BEYOND_APPLIED"

    def test_a_record_outside_the_span_moving_leaves_the_digest_alone(self, tree):
        """The negative half, so the test above is about the span.

        Without this, a digest over the whole file would pass the test above.
        That would be a different claim — "this capture changed" rather than
        "the text this range is about changed" — and it would send a person
        back to re-read a span whose content never moved.
        """
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(10))
        write_card(tree, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-4\n"))
        before = only_card(tree)["judged"]["review_ranges"][0]["content_identity"]

        rows = messages(10)
        rows[1]["content"] = "卡已经读过的那一段里的一句话"
        write_messages(tree, "people/x/oc_x", rows)
        after = only_card(tree)["judged"]["review_ranges"][0]["content_identity"]
        assert before == after

    def test_a_reserialisation_is_the_same_records_and_the_same_digest(
            self, tmp_path):
        """Canonical, so the collector's key order is not a version.

        A collector that rewrites `messages.jsonl` with the same records and
        the keys in a different order has not changed the source. A digest taken
        over the raw line would say it had, and every confirmation taken before
        that rewrite would go back to a person for no reason.
        """
        one = tmp_path / "one.jsonl"
        two = tmp_path / "two.jsonl"
        record = {"message_position": "7", "message_id": "om_7",
                  "content": "x", "deleted": False, "updated": False}
        one.write_text(json.dumps(record) + "\n", encoding="utf-8")
        two.write_text(json.dumps({key: record[key] for key in
                                   ("updated", "content", "deleted",
                                    "message_id", "message_position")}) + "\n",
                       encoding="utf-8")
        assert ud.range_content_digest(one, 5, 9) == \
            ud.range_content_digest(two, 5, 9)
        assert ud.range_content_digest(one, 5, 9)[0] is not None

    def test_a_field_added_to_a_record_is_a_different_revision(self, tmp_path):
        """And the other half: a record is not the same record with a new key.

        The digest is not "the text of the message"; it is "this record". A
        collector that starts recording an extra field has produced a different
        record, and a confirmation taken over the older shape should come back
        to a person rather than be carried forward on a comparison nobody made.
        """
        one = tmp_path / "one.jsonl"
        two = tmp_path / "two.jsonl"
        record = {"message_position": "7", "message_id": "om_7", "content": "x"}
        one.write_text(json.dumps(record) + "\n", encoding="utf-8")
        two.write_text(json.dumps({**record, "reactions": []}) + "\n",
                       encoding="utf-8")
        assert ud.range_content_digest(one, 5, 9)[0] != \
            ud.range_content_digest(two, 5, 9)[0]

    def test_a_span_holding_no_record_is_unidentifiable_and_says_so(self, tmp_path):
        """Null with a reason, never a digest of nothing.

        Reachable through this function and not through `judge_card`: a
        `BEYOND_APPLIED` span starts one past the card's reach and runs to the
        source's maximum, and that maximum came out of the same file, so it
        always names at least one record. The branch is kept because the
        function is the contract — a caller holding a span from somewhere else
        must get "I cannot identify this" and not the digest of the empty
        string, which would compare equal to every other empty span.
        """
        path = tmp_path / "messages.jsonl"
        path.write_text(json.dumps(messages(3)[0]) + "\n", encoding="utf-8")
        digest, why = ud.range_content_digest(path, 5, 9)
        assert digest is None
        assert "no record in the messages file carries a position in 5–9" in why
        # And the same file over a span it does hold is identified, so the
        # assertion above is about the span and not about the file.
        assert ud.range_content_digest(path, 0, 0)[0] is not None

    def test_an_unreadable_span_is_null_with_its_own_reason(self, tmp_path):
        """Three ways to have no digest, and none of them is an empty digest."""
        missing = tmp_path / "absent.jsonl"
        digest, why = ud.range_content_digest(missing, 5, 9)
        assert digest is None and "is not a file" in why

        broken = tmp_path / "broken.jsonl"
        broken.write_text("{not json}\n", encoding="utf-8")
        digest, why = ud.range_content_digest(broken, 5, 9)
        assert digest is None and "not JSON" in why

        positional = tmp_path / "no_span.jsonl"
        digest, why = ud.range_content_digest(positional, None, None)
        assert digest is None and "names no positions" in why

    def test_a_document_range_takes_the_digest_the_manifest_recorded(self):
        """A document has no records here, so its revision is the manifest's.

        `content_sha256` is measured by the collector that wrote the file, and
        it is the only digest this module has for a document. Copying it is not
        this reader claiming to have read the document; it is the same value the
        range comparison above already used.
        """
        row = {"content_sha256": "b" * 64}
        assert ud._content_identity(row, "WHOLE_DOCUMENT", None, None,
                                    Path("/nonexistent")) == ("b" * 64, "")

        digest, why = ud._content_identity({}, "WHOLE_DOCUMENT", None, None,
                                           Path("/nonexistent"))
        assert digest is None
        assert "records no digest for this document" in why

    def test_a_chat_range_always_carries_an_identity(self, tree):
        """The end-to-end half, and the one the writer consumes.

        `content_identity_why` is absent rather than null here, following `lag`
        and `unresolved_why` above: an explanation that does not apply is not
        emitted. What the writer needs is that the key it copies is a real
        digest for every range this module actually produces, so that is what
        is asserted.
        """
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(10))
        write_card(tree, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-4\n"))
        range_ = only_card(tree)["judged"]["review_ranges"][0]
        assert len(range_["content_identity"]) == 64
        assert set(range_["content_identity"]) <= set("0123456789abcdef")
        assert "content_identity_why" not in range_

    def test_the_card_digest_is_the_cards_own_text(self, tree):
        """The other version half: the understanding baseline, not the source.

        A card rewritten in place keeps its path and can keep every span it
        cites, so the path does not name the text a proposal was computed
        against — and a person who read the older text would be recorded as
        having confirmed a proposal about the newer one.
        """
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(10))
        body = heading_card(f"- `{CHAT}` message_position 0-4\n")
        write_card(tree, "people", "x.md", body)
        first = only_card(tree)["card_digest"]

        write_card(tree, "people", "x.md", body)
        assert only_card(tree)["card_digest"] == first

        write_card(tree, "people", "x.md", body + "\n补了一句话。\n")
        assert only_card(tree)["card_digest"] != first


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
        root = _cli_records_root()
        if not readable_dir(root / "information" / "raw"):
            pytest.skip(f"no capture tree at {root}")
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

    def test_the_partial_line_names_every_kind_of_unchecked_citation(self, tree):
        """Three kinds, told apart in the sentence a person reads.

        A citation that named no reach was not compared; one that resolved to
        nothing was not read; one that quoted a digest the manifest could not
        be compared with had nothing to compare against. A partial line naming
        only the first leaves a reader to conclude the rest were checked — and
        a partial line that named none of them would end at the separator with
        nothing after it, which is what this test was written after seeing.
        """
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", messages(4))
        write_capture(tree, "groups/g/oc_g", chat_manifest())
        write_document(tree, "people/x/oc_x/links/d",
                       content_sha256="deadbeef" + "0" * 56)
        write_card(tree, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-3\n"
                                f"- `{GROUP}`\n"
                                f"- `information/raw/feishu/chats/absent/oc_a/"
                                f"messages.jsonl`\n"
                                f"- `{DOC}`（sha `beef1234`）\n"))
        done = cli("work", "understanding", "--records-root", str(tree))
        assert done.returncode == 0, done.stderr
        assert "部分：1 条引文可比" in done.stdout
        assert "1 条没有可比的位置" in done.stdout
        assert "1 条没有对应的源" in done.stdout
        assert "1 条引文所引的摘要与 manifest 记的那份无法比对" in done.stdout

    def test_the_entry_point_does_not_call_a_mismatch_a_version_change(self, tree):
        """The sentence a person acts on, on the tree that produced the defect.

        With no predecessor recorded there is no comparison to report, and the
        live layer is where this was read aloud: a citation of `b9fe2535` — a
        digest in none of that document's twelve committed versions — was
        rendered as 「文档已换版 摘要 b9fe2535 → 7a586f53」.
        """
        write_document(tree, "people/x/oc_x/links/d")
        write_card(tree, "people", "x.md", heading_card(f"- `{DOC}`（sha `beef1234`）\n"))
        done = cli("work", "understanding", "--records-root", str(tree))
        assert done.returncode == 0, done.stderr
        assert "文档已换版" not in done.stdout
        assert "manifest 未记录上一版，无从比对" in done.stdout
        assert "引文摘要不是现版" in done.stdout
        assert "manifest 未记录上一版" in done.stdout

    def test_a_citation_of_a_version_the_manifest_does_not_record_says_so(self, tree):
        document = write_document(tree, "people/x/oc_x/links/d",
                                  prev_content_sha256="c" * 64)
        write_card(tree, "people", "x.md", heading_card(f"- `{DOC}`（sha `beef1234`）\n"))
        done = cli("work", "understanding", "--records-root", str(tree))
        assert done.returncode == 0, done.stderr
        assert "文档已换版" not in done.stdout
        assert "引文摘要不在记录的两版里" in done.stdout
        assert (f"引的 beef1234 ≠ 现版 {document['content_sha256']}"
                f"、≠ 上一版 {'c' * 64}") in done.stdout

    def test_a_recorded_predecessor_is_still_rendered_as_a_version_change(self, tree):
        """The control: the standing that does support the arrow keeps it.

        Without this, a fix that stopped saying 「文档已换版」 entirely would
        pass the two tests above.
        """
        document = write_document(tree, "people/x/oc_x/links/d",
                                  prev_content_sha256="beef1234" + "0" * 56)
        write_card(tree, "people", "x.md", heading_card(f"- `{DOC}`（sha `beef1234`）\n"))
        done = cli("work", "understanding", "--records-root", str(tree))
        assert done.returncode == 0, done.stderr
        assert "文档已换版" in done.stdout
        assert f"摘要 beef1234 → {document['content_sha256']}" in done.stdout
        assert "引文正是 manifest 记的上一版" in done.stdout

    def test_a_comparison_made_without_the_body_says_so_on_the_line(self, tree):
        """「一致」 is the same word for two different comparisons.

        A `web_capture`'s digest names a body kept under an asset root this
        reader is not given, so the comparison is made and reported as 「一致」
        — the same word a card that agrees with the file beside its manifest
        gets. The standing travels on the line for exactly that reason, and the
        control below is the other half: if it were printed unconditionally it
        would say nothing.
        """
        where = tree / "information" / "raw" / "web" / "ref" / "x"
        where.mkdir(parents=True)
        (where / "manifest.json").write_text(json.dumps({
            "schema": "serveros/web-capture.v1",
            "source": {"provider": "web",
                       "requested_url": "https://example.invalid/a",
                       "final_url": "https://example.invalid/a",
                       "redirected": False},
            "fetch": {"at": "2026-09-23T18:31+08:00", "http_status": 200,
                      "sha256": "beef1234" + "0" * 56, "captured_by": "curl"},
            "identity_observed": {"title": "T"},
        }, ensure_ascii=False), encoding="utf-8")
        web = "information/raw/web/ref/x/content.md"
        write_card(tree, "people", "x.md", heading_card(f"- `{web}`（sha `beef1234`）\n"))
        done = cli("work", "understanding", "--records-root", str(tree))
        assert done.returncode == 0, done.stderr
        assert "一致" in done.stdout
        assert "记录的是别处持有的正文（此处未比对字节）" in done.stdout

        document = write_document(tree, "people/x/oc_x/links/d")
        write_card(tree, "people", "x.md", heading_card(
            f"- `{DOC}`（sha `{document['content_sha256'][:8]}`）\n"))
        done = cli("work", "understanding", "--records-root", str(tree))
        assert done.returncode == 0, done.stderr
        assert "一致" in done.stdout
        assert "记录的是别处持有的正文" not in done.stdout

    def test_the_line_says_which_of_the_two_hashes_the_record_was_taken_of(self, tree):
        """The same 「一致」, and now the other comparison that reads alike.

        `RECORDED_IS_THIS_FILES_CONTENT_DIGEST` is *also* a digest of this file
        — of its bytes up to the newline the collector appends on the way out.
        So the line says 「一致」 and stays true, while a reader who runs
        `sha256sum content.md` gets a value the manifest does not contain. The
        standing travels on the line for exactly that reason, and the control
        below is the other half: printed unconditionally it would say nothing.
        """
        recorded = hashlib.sha256(DOC_BODY[:-1].encode("utf-8")).hexdigest()
        assert DOC_BODY.endswith("\n")
        assert recorded != hashlib.sha256(DOC_BODY.encode("utf-8")).hexdigest()
        write_document(tree, "people/x/oc_x/links/d", content_sha256=recorded)
        write_card(tree, "people", "x.md", heading_card(f"- `{DOC}`（sha `{recorded}`）\n"))
        done = cli("work", "understanding", "--records-root", str(tree))
        assert done.returncode == 0, done.stderr
        assert "一致" in done.stdout
        assert "记录是本文件内容的摘要（写的人落盘时补的换行不计）" in done.stdout

        honest = hashlib.sha256(DOC_BODY.encode("utf-8")).hexdigest()
        write_document(tree, "people/x/oc_x/links/d")
        write_card(tree, "people", "x.md", heading_card(f"- `{DOC}`（sha `{honest}`）\n"))
        done = cli("work", "understanding", "--records-root", str(tree))
        assert done.returncode == 0, done.stderr
        assert "一致" in done.stdout
        assert "记录是本文件内容的摘要" not in done.stdout

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

@pytest.mark.skipif(not readable_dir(LIVE_RECORDS / "information" / "raw"),
                    reason=f"no records checkout at {LIVE_RECORDS}")
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
