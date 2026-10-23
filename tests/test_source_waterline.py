"""Two waterlines per source, and the middle gap a maximum cannot carry.

WHY THIS FILE EXISTS
--------------------
`docs/COLLABORATION-CONTRACT.md` §6 states the rule this module was written for:

    任何中间 gap 都不能被最大位置掩盖。

Read on 2026-09-23, nothing in the fabric measured it. `understanding_delta`
compares a card's reach to the source's **maximum** and subtracts, which answers
"how far is the card behind" and cannot answer "is anything missing". A source
holding positions 0–4 and 9–13 reaches 13, and every scalar summary of it — the
maximum, `count`, `latest_create_time` — reports it as complete up to 13 while
four positions are absent from the middle. The gap is not smaller than the
maximum; it is invisible in it, so no arrangement of one number per source can
report it.

Every property below is asserted here for the first time. The load-bearing ones:

  * `.test_every_position_understood_and_the_source_still_not_up_to_date` — an
    empty `lag` and a non-`UP_TO_DATE` state at the same time. A scalar
    implementation cannot produce that pair, so this is the test it fails.
  * `.test_the_collector_stopping_early_does_not_read_as_no_lag` — an open tail
    is `TAIL_UNKNOWN`, because "nothing past the extent is understood" and
    "nothing past the extent exists" are different sentences.
  * the producer tables, so a future field has to name its source before it can
    leave the module.

The live test at the end walks the real records root and asserts the waterlines'
denominator equals the number of capture objects actually there, so a fixture
that drifts from the tree fails as a drift and not as an edit.
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
from live_checkouts import readable_dir  # noqa: E402

LIVE_RECORDS = Path("/data/home/vince/workspace/records")


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(
        "serveros_intake_" + name, REPO_ROOT / "intake" / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = _load("capture_coverage", "capture_coverage.py")
ud = _load("understanding_delta", "understanding_delta.py")
sw = _load("source_waterline", "source_waterline.py")


def cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, cwd=str(REPO_ROOT))


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

# Two coordinate systems, and a fixture that mixes them proves nothing. `CHAT` is
# how a CARD writes a path: relative to the records root, because that is what a
# reader of the card can open. `KEY` is how the capture side names the same
# source: relative to `information/raw`, because that is the tree it walks.
CHAT = "information/raw/feishu/chats/people/x/oc_x/messages.jsonl"
ABSENT = "information/raw/feishu/chats/people/y/oc_y/messages.jsonl"
DOC = "information/raw/feishu/chats/people/x/oc_x/links/d/content.md"
RAW = Path("information/raw/feishu/chats")
KEY = "feishu/chats/people/x/oc_x/manifest.json"
KEY_DOC = "feishu/chats/people/x/oc_x/links/d/manifest.json"


def chat_sync(**over) -> dict:
    sync = {"mode": "incremental", "method": "im/chat-messages-list",
            "msg_count": 10, "remote_unique_message_count": 10,
            "fetched_page_count": 1, "pagination_complete": True,
            "earliest_create_time": "2026-09-11 09:45",
            "latest_create_time": "2026-09-11 10:07",
            "last_message_id": "om_x"}
    sync.update(over)
    return sync


def chat_manifest(**over) -> dict:
    document = {"contact": {"name": "X", "open_id": "ou_x"},
                "chat": {"chat_id": "oc_x", "chat_type": "p2p", "name": "X"},
                "sync": chat_sync()}
    document.update(over)
    return document


def document_manifest(**over) -> dict:
    document = {"source_message_id": "om_x", "url": "https://example.invalid/d",
                "type": "docx", "title": "T", "fetched_at": "2026-09-07",
                "content_sha256": "a" * 64, "redaction_applied": False}
    document.update(over)
    return document


@pytest.fixture
def tree(tmp_path) -> Path:
    """A records tree a test owns: an understanding layer and a capture tree."""
    records = tmp_path / "records"
    for relative in ("information/understanding/people",
                     "information/raw/feishu/chats"):
        (records / relative).mkdir(parents=True)
    return records


def write_capture(records: Path, relative: str, document) -> Path:
    """One capture object, at the place the collectors write manifests."""
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


def positions_messages(positions) -> list[dict]:
    """One record per position, so a fixture can hold a hole."""
    return [{"message_position": str(position), "message_id": f"om_{position}",
             "content": "x"} for position in positions]


def write_card(records: Path, scope: str, name: str, body: str) -> Path:
    path = records / "information" / "understanding" / scope / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def heading_card(sources: str) -> str:
    return "# 卡\n\n## 当前\n\n内容。\n\n## Sources\n" + sources


def object_of(tree: Path, relative: str = "people/x/oc_x", **over) -> dict:
    """One capture object, read by the reader that owns that tree.

    Built through `capture_coverage.read_root` and not by hand: a hand-written
    object would be a second opinion about what a capture object is, and it
    would agree with this module until one of the two moved.
    """
    write_capture(tree, relative, over.pop("manifest", chat_manifest(**over)))
    objects = cc.read_root(tree)["objects"]
    assert len(objects) == 1, objects
    return objects[0]


def runs_at(tree: Path, relative: str = "people/x/oc_x") -> dict:
    return ud.read_position_runs(tree / RAW / relative / "messages.jsonl")


def capture_at(tree: Path, positions, relative: str = "people/x/oc_x", **over):
    obj = object_of(tree, relative, **over)
    write_messages(tree, relative, positions_messages(positions))
    return sw.capture_waterline(obj, lambda: runs_at(tree, relative))


def row(kind: str, low=None, high=None, **extra) -> dict:
    """A measurement row of the shape `understanding_delta` emits."""
    return {"kind": kind, "manifest": KEY, "applied_low": low, "applied": high,
            **extra}


# --------------------------------------------------------------------------
# the rule this module exists for
# --------------------------------------------------------------------------

class TestAMaximumCannotMaskAMiddleGap:
    def test_the_scalar_summary_is_kept_and_the_hole_is_kept_beside_it(self, tree):
        """The same source, read twice: once as an extent, once as runs.

        `high` is what every existing reader reports and it is correct about
        what it answers. The four positions under it are the fact it does not
        carry, and they are what this module adds rather than replaces.
        """
        capture = capture_at(tree, [0, 1, 2, 3, 4, 9, 10, 11, 12, 13])
        assert capture["high"] == 13
        assert capture["runs"] == [[0, 4], [9, 13]]
        assert capture["gaps"] == [[5, 8]]

    def test_every_position_understood_and_the_source_still_not_up_to_date(self, tree):
        """THE LOAD-BEARING PAIR: an empty lag and a non-UP_TO_DATE state.

        Understanding covers 0–13, which is every position the capture holds
        anything at. The set difference is therefore empty. A source with four
        positions absent from the middle is not up to date, and an
        implementation that computes the lag as `capture.high - understood.high`
        reports exactly the opposite — so this is the test such an
        implementation fails.
        """
        capture = capture_at(tree, [0, 1, 2, 3, 4, 9, 10, 11, 12, 13])
        understood = sw.understood_waterline(
            KEY, "POSITIONAL", [row("POSITION", 0, 13)])
        compared = sw.compare(capture, understood)
        assert compared["lag"] == []
        assert compared["state"] == "LABELS_NOT_CONTIGUOUS"
        assert compared["state"] != "UP_TO_DATE"
        assert compared["unread_capture_gaps"] == [[5, 8]]

    def test_the_hole_is_reported_even_when_the_lag_is_empty(self, tree):
        """Stated on its own, because it is the sentence a reader acts on."""
        capture = capture_at(tree, [0, 1, 2, 3, 4, 9, 10, 11, 12, 13])
        understood = sw.understood_waterline(
            KEY, "POSITIONAL", [row("POSITION", 0, 13)])
        compared = sw.compare(capture, understood)
        assert compared["lag"] == []
        assert compared["unread_capture_gaps"] == [[5, 8]]
        assert "not contiguous" in " ".join(compared["capture"]["why"])

    def test_a_hole_the_records_corroborate_is_called_missing_material(self, tree):
        """The same four absent positions, and this time a record says so.

        This and the test above are the same measurement with different
        evidence, and the two states are the point: a hole is measured, missing
        material is a conclusion, and `remote_unique_message_count` larger than
        `msg_count` is a record that draws the conclusion.
        """
        capture = capture_at(tree, [0, 1, 2, 3, 4, 9, 10, 11, 12, 13],
                             sync=chat_sync(remote_unique_message_count=14))
        assert capture["state"] == "GAPPED"
        assert "hole in the evidence" in " ".join(capture["why"])
        understood = sw.understood_waterline(
            KEY, "POSITIONAL", [row("POSITION", 0, 13)])
        compared = sw.compare(capture, understood)
        assert compared["state"] == "GAPPED"
        assert compared["lag"] == []

    def test_an_unfinished_fetch_also_corroborates_a_hole(self, tree):
        capture = capture_at(tree, [0, 1, 2, 3, 4, 9, 10, 11, 12, 13],
                             sync=chat_sync(pagination_complete=False))
        assert capture["state"] == "GAPPED"
        assert "pagination_complete" in " ".join(capture["why"])

    def test_the_live_tree_reading_that_produced_this_rule(self, tree):
        """The shape all three live non-contiguous captures have.

        Measured 2026-09-23: nanjing-office holds 4956 positions under labels
        0–5379, and its own account says it wrote 4956 and the source holds
        4956. Calling that `GAPPED` would send a person looking for 424
        messages that nothing on record says exist.
        """
        capture = capture_at(tree, [0, 1, 2, 3, 4, 9, 10, 11, 12, 13])
        assert capture["declared"]["facts"]["msg_count"] == 10
        assert capture["declared"]["facts"]["remote_unique_message_count"] == 10
        assert capture["declared"]["facts"]["pagination_complete"] is True
        assert capture["state"] == "LABELS_NOT_CONTIGUOUS"
        assert "the records do not choose" in " ".join(capture["why"])

    def test_a_hole_does_not_open_the_tail(self, tree):
        """Both ends are known; only the middle is missing.

        A tail is about where the source ENDS. Conflating the two would report
        a closed, complete-from-both-ends capture as one still being fetched,
        and the person reading it would wait for a fetch that already ended.
        """
        capture = capture_at(tree, [0, 1, 2, 3, 4, 9, 10, 11, 12, 13])
        assert capture["tail"] == "CLOSED"

    def test_a_run_starting_above_zero_is_not_a_hole(self, tree):
        """Where the captured conversation begins is not a gap in it."""
        capture = capture_at(tree, [3, 4, 5])
        assert capture["gaps"] == []
        assert capture["state"] == "COMPLETE"
        assert capture["tail"] == "CLOSED"


class TestTheTailIsNotTheExtent:
    def test_a_finished_fetch_closes_the_tail(self, tree):
        capture = capture_at(tree, list(range(10)))
        assert capture["tail"] == "CLOSED"
        assert capture["state"] == "COMPLETE"
        compared = sw.compare(capture, sw.understood_waterline(
            KEY, "POSITIONAL", [row("POSITION", 0, 9)]))
        assert compared["state"] == "UP_TO_DATE"
        assert compared["why"] == []

    def test_the_collector_stopping_early_does_not_read_as_no_lag(self, tree):
        """THE SECOND LOAD-BEARING ONE: an open tail with an empty lag.

        The card has read every position the capture holds. The collector says
        it stopped before the source ran out. "Everything captured is
        understood" is then true and "there is nothing left" is not, and the
        only word that says the second is TAIL_UNKNOWN.
        """
        capture = capture_at(tree, list(range(10)),
                             sync=chat_sync(pagination_complete=False))
        assert capture["tail"] == "OPEN"
        assert capture["state"] == "INCOMPLETE"
        compared = sw.compare(capture, sw.understood_waterline(
            KEY, "POSITIONAL", [row("POSITION", 0, 9)]))
        assert compared["lag"] == []
        assert compared["state"] == "TAIL_UNKNOWN"
        assert compared["state"] != "UP_TO_DATE"
        assert "lower bound" in compared["why"][-1]

    def test_a_disagreeing_independent_count_opens_the_tail(self, tree):
        """The collector wrote 10 and the source's own count is 10 — until it is not.

        The sentence asserted on is `capture_coverage`'s own wording, copied
        verbatim: a module that re-derived this comparison would have had to
        write a second sentence, and the two would agree until one of them
        changed. Asserting on the copied text is what makes that visible.
        """
        capture = capture_at(tree, list(range(10)),
                             sync=chat_sync(remote_unique_message_count=12))
        assert capture["state"] == "INCOMPLETE"
        assert capture["tail"] == "OPEN"
        assert "the source's own count is 12" in " ".join(capture["why"])

    def test_a_manifest_that_records_neither_opens_the_tail(self, tree):
        """Nothing on the manifest says the fetch finished."""
        capture = capture_at(tree, list(range(10)),
                             sync={"msg_count": 10})
        assert capture["state"] == "UNDECIDABLE"
        assert capture["tail"] == "OPEN"
        assert "pagination_complete" in " ".join(capture["why"])

    def test_both_defects_at_once_both_stay_readable(self, tree):
        """A hole and an unfinished fetch, and neither one absorbs the other.

        The state is the hole because a hole appears in no other record — an
        `INCOMPLETE` component is already on the capture object — while the hole
        appears nowhere else. The unfinished fetch is still readable in
        `declared`, in `tail`, and in `why`, which is what makes the ordering a
        choice about emphasis rather than about what is lost.
        """
        capture = capture_at(tree, [0, 4, 9], sync=chat_sync(pagination_complete=False))
        assert capture["state"] == "GAPPED"
        assert capture["gaps"] == [[1, 3], [5, 8]]
        assert capture["tail"] == "OPEN"
        assert capture["declared"]["components"]["messages"] == "INCOMPLETE"
        assert len(capture["why"]) >= 3


class TestTheCaptureSideNamesWhatItCannotState:
    def test_no_messages_file_is_unreadable_and_not_zero_positions(self, tree):
        """A source with no readable positions is not a source with none.

        Reporting `high: 0` here would say the capture reached position zero,
        which is a number this module did not read.
        """
        obj = object_of(tree)
        capture = sw.capture_waterline(obj, lambda: runs_at(tree))
        assert capture["kind"] == "POSITIONAL"
        assert capture["state"] == "UNREADABLE"
        assert capture["high"] is None
        assert capture["tail"] == "OPEN"
        assert "messages file" in " ".join(capture["why"])

    def test_a_duplicate_position_is_named_and_does_not_bend_the_runs(self, tree):
        """A repeated position disagrees with a count; it does not make a gap."""
        write_messages(tree, "people/x/oc_x",
                       positions_messages([0, 1, 2, 2, 3]))
        capture = sw.capture_waterline(object_of(tree), lambda: runs_at(tree))
        assert capture["duplicates"] == [2]
        assert capture["runs"] == [[0, 3]]
        assert capture["gaps"] == []
        assert capture["state"] == "DUPLICATED"

    def test_a_document_capture_has_no_positions_and_says_so(self, tree):
        """A document is not a chat with an empty messages file."""
        write_capture(tree, "people/x/oc_x/links/d", document_manifest())
        obj, = cc.read_root(tree)["objects"]
        capture = sw.capture_waterline(obj, lambda: pytest.fail(
            "a document source must not have a messages file looked for"))
        assert capture["kind"] == "DOCUMENT"
        assert capture["state"] == "COMPLETE"
        assert capture["tail"] is None
        assert capture["runs"] == []
        assert capture["high"] is None

    def test_a_document_whose_digest_is_short_is_incomplete(self, tree):
        write_capture(tree, "people/x/oc_x/links/d",
                      document_manifest(content_sha256="a" * 12))
        obj, = cc.read_root(tree)["objects"]
        capture = sw.capture_waterline(obj, lambda: runs_at(tree))
        assert capture["state"] == "INCOMPLETE"
        assert "does not let anyone re-check" in " ".join(capture["why"])

    def test_a_manifest_that_is_not_a_capture_is_unreadable(self, tree):
        write_capture(tree, "people/x/oc_x", {"something": "else"})
        obj, = cc.read_root(tree)["objects"]
        capture = sw.capture_waterline(obj, lambda: runs_at(tree))
        assert capture["kind"] == "UNREADABLE"
        assert capture["state"] == "UNREADABLE"
        compared = sw.compare(capture, sw.understood_waterline(KEY, "POSITIONAL", []))
        assert compared["state"] == "UNREADABLE"
        assert "absence of a comparison" in " ".join(compared["why"])

    def test_a_partial_component_outside_the_waterline_is_named_and_not_folded_in(self, tree):
        """`links` being short does not decide whether a POSITION is behind.

        It is copied into `declared` and named in `why` so a reader is not left
        thinking a partial capture is a settled one — and it does not move the
        state, because the state is about the source's own reach.
        """
        capture = capture_at(tree, list(range(4)),
                             links={"count": 2, "expanded": ["a"]})
        assert capture["state"] == "COMPLETE"
        assert capture["declared"]["components"]["links"] == "INCOMPLETE"
        assert "outside this waterline" in " ".join(capture["why"])


class TestWhatUnderstandingAccountsFor:
    def test_two_cards_citing_one_source_cover_it_once(self, tree):
        """Union and not concatenation: a doubled reach reads as further along."""
        understood = sw.understood_waterline(
            KEY, "POSITIONAL", [row("POSITION", 0, 4), row("POSITION", 3, 9)])
        assert understood["runs"] == [[0, 9]]
        assert understood["spans"] == [[0, 4], [3, 9]]

    def test_reaches_that_skip_positions_are_gapped(self, tree):
        understood = sw.understood_waterline(
            KEY, "POSITIONAL", [row("POSITION", 0, 3), row("POSITION", 7, 9)])
        assert understood["state"] == "GAPPED"
        assert understood["gaps"] == [[4, 6]]

    def test_a_citation_with_no_reach_accounts_for_nothing(self, tree):
        """`WHOLE_FILE` is a citation that did not say how far it read."""
        understood = sw.understood_waterline(
            KEY, "POSITIONAL", [row("WHOLE_FILE")])
        assert understood["state"] == "NO_REACH"
        assert understood["runs"] == []
        assert "not evidence of being current" in " ".join(understood["why"])

    def test_a_reach_that_could_not_be_compared_is_not_a_reach_that_absent(self, tree):
        understood = sw.understood_waterline(
            KEY, "POSITIONAL",
            [row("WHOLE_FILE", unresolved_why="positions not readable")])
        assert understood["state"] == "UNMEASURABLE"
        assert "not a question this module answered" in " ".join(understood["why"])

    def test_a_source_nothing_cites_is_none_rather_than_complete(self, tree):
        understood = sw.understood_waterline(KEY, "POSITIONAL", [])
        assert understood["state"] == "NONE"
        assert understood["runs"] == []
        assert understood["why"] == []

    def test_a_position_in_a_document_is_refused_rather_than_reported(self, tree):
        """Two coordinate systems confused is a defect here, not a reach there."""
        with pytest.raises(sw.Refused, match="document capture"):
            sw.understood_waterline(KEY_DOC, "DOCUMENT",
                                    [row("POSITION", 0, 9)])

    def test_an_unreadable_chat_capture_does_not_manufacture_an_overreach(self, tree):
        """A chat whose positions cannot be read has no extent to be compared to.

        The runs are empty for want of a file, not for want of messages, so every
        reach a card claims would read as past the capture's extent. Calling that
        INCONSISTENT would report a card over-reaching on the strength of this
        module's own failure to read — the opposite of what the comparison is for.
        """
        capture = sw.capture_waterline(object_of(tree), lambda: runs_at(tree))
        assert capture["kind"] == "POSITIONAL" and capture["state"] == "UNREADABLE"
        for rows in ([], [row("POSITION", 0, 9)]):
            compared = sw.compare(capture, sw.understood_waterline(
                KEY, "POSITIONAL", rows))
            assert compared["state"] == "UNREADABLE", compared["state"]
            assert compared["beyond_source"] == []
            assert compared["lag"] == []


class TestTheComparisonKeepsTheFactsApart:
    def test_a_reach_past_the_capture_is_reported_and_not_clamped(self, tree):
        """Clamping would make a card citing 0–999 against 0–13 look current."""
        capture = capture_at(tree, list(range(14)))
        understood = sw.understood_waterline(
            KEY, "POSITIONAL", [row("POSITION", 0, 999)])
        compared = sw.compare(capture, understood)
        assert compared["beyond_source"] == [[14, 999]]
        assert compared["state"] == "INCONSISTENT"
        assert "not the same fact as being behind" in " ".join(compared["why"])

    def test_a_hole_in_the_capture_is_read_before_the_lag(self, tree):
        """The order of the sentences is the order of the consequences."""
        capture = capture_at(tree, [0, 9, 10])
        understood = sw.understood_waterline(KEY, "POSITIONAL", [row("POSITION", 0, 0)])
        compared = sw.compare(capture, understood)
        assert compared["lag"] == [[9, 10]]
        assert "holds no position at 1–8" in compared["why"][0]
        assert "captured and not accounted for" in compared["why"][1]

    def test_a_document_is_never_up_to_date(self, tree):
        """Nothing positional exists to disagree, so UP_TO_DATE would be a claim.

        A document whose digest no longer matches has no position to be behind
        on, and an implementation that reported UP_TO_DATE for it would be
        reporting the comparison it did not make.
        """
        write_capture(tree, "people/x/oc_x/links/d", document_manifest())
        obj, = cc.read_root(tree)["objects"]
        capture = sw.capture_waterline(obj, lambda: runs_at(tree))
        compared = sw.compare(capture, sw.understood_waterline(
            KEY_DOC, "DOCUMENT", [row("WHOLE_FILE")]))
        assert compared["state"] == "NOT_POSITIONAL"
        assert compared["state"] != "UP_TO_DATE"
        assert "DIGEST measurement" in " ".join(compared["why"])
        assert compared["lag"] == []

    def test_a_source_no_capture_object_holds_still_gets_a_row(self, tree):
        """Without this row, a card citing an absent source appears nowhere."""
        wrote = sw.unresolved_row(ABSENT, "no capture object matches")
        assert wrote["state"] == "UNRESOLVED"
        assert wrote["capture"] is None
        assert wrote["lag"] == []
        assert wrote["why"] == ["no capture object matches"]


class TestTheIntervalAlgebra:
    def test_runs_of_unions_overlapping_spans(self):
        assert sw.runs_of([(0, 4), (3, 9)]) == [[0, 9]]
        assert sw.runs_of([(0, 4), (5, 9)]) == [[0, 9]]
        assert sw.runs_of([(0, 4), (9, 13)]) == [[0, 4], [9, 13]]

    def test_runs_of_ignores_a_reversed_span_and_a_missing_end(self):
        assert sw.runs_of([(9, 0), (None, 3), (4, None)]) == []

    def test_gaps_of_names_the_middle_only(self):
        assert sw.gaps_of([[0, 4], [9, 13]]) == [[5, 8]]
        assert sw.gaps_of([[3, 4], [9, 13]]) == [[5, 8]]
        assert sw.gaps_of([[0, 13]]) == []
        assert sw.gaps_of([]) == []

    def test_gaps_of_finds_nothing_between_touching_runs(self):
        """`[5, 4]` is not a gap and must never be produced as one.

        `runs_of` merges touching spans, so this input does not arise from the
        module's own path — which is exactly why the boundary needs asserting
        here rather than being left to a path that never reaches it.
        """
        assert sw.gaps_of([[0, 4], [5, 9]]) == []
        assert sw.gaps_of([[0, 4], [6, 9]]) == [[5, 5]]

    def test_below_is_a_set_difference_and_not_an_arithmetic_one(self):
        assert sw.below([[0, 4], [9, 13]], [[0, 13]]) == []
        assert sw.below([[0, 13]], [[0, 2], [7, 13]]) == [[3, 6]]
        assert sw.below([[0, 13]], [[5, 8]]) == [[0, 4], [9, 13]]
        assert sw.below([[0, 13]], []) == [[0, 13]]
        assert sw.below([], [[0, 13]]) == []

    def test_below_normalizes_its_covered_side(self):
        """The walk steps through `covered` once in order, so it is normalized."""
        assert sw.below([[0, 9]], [[7, 9], [0, 2]]) == [[3, 6]]

    def test_above_reports_everything_when_there_is_no_ceiling(self):
        assert sw.above([[0, 9]], None) == [[0, 9]]
        assert sw.above([[0, 9]], 4) == [[5, 9]]
        assert sw.above([[0, 9]], 9) == []
        assert sw.above([], None) == []

    def test_a_long_run_list_is_shortened_and_the_shortening_is_visible(self):
        """A truncated line says how much it elided, so it cannot pass for whole."""
        runs = [[n, n] for n in range(20)]
        short = sw._runs_text(runs)
        assert short.startswith("0 1 2")
        assert "另 8 段" in short
        assert "全部 20 段" in short
        assert sw._runs_text(runs[:3]) == "0 1 2"
        assert sw._runs_text(runs, limit=None).count(" ") == 19


class TestTheProducerTables:
    def test_an_unnamed_capture_field_is_refused(self, monkeypatch):
        obj = {"path": KEY, "shape": "chat_capture",
               "coverage": {"components": {}}}
        original = sw.capture_waterline

        def smuggle(object_, runs_of_source):
            written = original(object_, runs_of_source)
            written["invented"] = 1
            return written
        # The guard is inside the function, so it is probed where it lives.
        with pytest.raises(sw.Refused, match="producer table"):
            sw.no_unsourced({"invented": 1}, sw.CAPTURE_PRODUCED, "capture")
        assert smuggle  # the real function is unchanged; the probe is the guard

    def test_every_table_refuses_a_key_it_does_not_name(self):
        for table, where in ((sw.CAPTURE_PRODUCED, "capture"),
                             (sw.UNDERSTOOD_PRODUCED, "understood"),
                             (sw.COMPARISON_PRODUCED, "comparison"),
                             (sw.WATERLINES_PRODUCED, "waterlines")):
            with pytest.raises(sw.Refused, match=where):
                sw.no_unsourced({"not_a_field": 1}, table, where)
            assert sw.no_unsourced(dict.fromkeys(table, None), table, where)

    def test_the_tables_and_what_the_module_emits_agree(self, tree):
        """Every key the module emits is one its table names, checked by emitting."""
        capture = capture_at(tree, list(range(4)))
        assert set(capture) == set(sw.CAPTURE_PRODUCED)
        understood = sw.understood_waterline(KEY, "POSITIONAL", [row("POSITION", 0, 3)])
        assert set(understood) == set(sw.UNDERSTOOD_PRODUCED)
        compared = sw.compare(capture, understood)
        assert set(compared) == set(sw.COMPARISON_PRODUCED)

    def test_the_states_are_the_declared_ones(self, tree):
        capture = capture_at(tree, list(range(4)))
        assert capture["kind"] in sw.CAPTURE_KINDS
        assert capture["state"] in sw.CAPTURE_STATES
        understood = sw.understood_waterline(KEY, "POSITIONAL", [])
        assert understood["state"] in sw.UNDERSTOOD_STATES
        assert sw.compare(capture, understood)["state"] in sw.COMPARISON_STATES


# --------------------------------------------------------------------------
# the verb, on a tree and not on a dict
# --------------------------------------------------------------------------

class TestTheVerbOnARealTree:
    def test_waterlines_composes_the_two_readers_over_a_capture_tree(self, tree):
        write_capture(tree, "people/x/oc_x",
                      chat_manifest(sync=chat_sync(remote_unique_message_count=14)))
        write_messages(tree, "people/x/oc_x",
                       positions_messages([0, 1, 2, 3, 4, 9, 10, 11, 12, 13]))
        write_card(tree, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-13\n"))
        document = sw.waterlines(tree, ud, cc.read_root(tree)["objects"])
        assert [r["source"] for r in document["sources"]] == [KEY]
        row_ = document["sources"][0]
        assert row_["state"] == "GAPPED"
        assert row_["lag"] == []
        assert row_["unread_capture_gaps"] == [[5, 8]]

    def test_the_same_tree_with_an_agreeing_account_is_not_called_gapped(self, tree):
        """One manifest field apart, and the state is a different sentence."""
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x",
                       positions_messages([0, 1, 2, 3, 4, 9, 10, 11, 12, 13]))
        write_card(tree, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-13\n"))
        document = sw.waterlines(tree, ud, cc.read_root(tree)["objects"])
        assert document["sources"][0]["state"] == "LABELS_NOT_CONTIGUOUS"

    def test_a_card_citing_an_uncaptured_source_gets_a_row_of_its_own(self, tree):
        """The capture tree holds nothing for it and the card still cites it.

        A waterline that listed only the capture objects would report this card
        as citing nothing, which is the silence this row exists to break.
        """
        write_card(tree, "people", "y.md",
                   heading_card(f"- `{ABSENT}` message_position 0-3\n"))
        document = sw.waterlines(tree, ud, cc.read_root(tree)["objects"])
        assert [r["source"] for r in document["sources"]] == [ABSENT]
        assert document["sources"][0]["state"] == "UNRESOLVED"
        assert document["sources"][0]["capture"] is None

    def test_the_verb_prints_the_gap_the_scalar_would_have_lost(self, tree):
        write_capture(tree, "people/x/oc_x",
                      chat_manifest(sync=chat_sync(remote_unique_message_count=14)))
        write_messages(tree, "people/x/oc_x",
                       positions_messages([0, 1, 2, 3, 4, 9, 10, 11, 12, 13]))
        write_card(tree, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-13\n"))
        run = cli("work", "waterline", "--records-root", str(tree))
        assert run.returncode == 0, run.stderr
        assert "GAPPED" in run.stdout
        assert "5–8" in run.stdout
        assert "capture 缺" in run.stdout

    def test_the_verb_carries_the_reading_and_not_only_the_state_word(self, tree):
        """An unexplained hole prints as unexplained, not as missing material.

        The state word alone would not be enough: a person reading `LABELS_NOT_
        CONTIGUOUS` in a list still needs the sentence that says what is and is
        not established, and this is where the verb either carries it or drops
        it.
        """
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x",
                       positions_messages([0, 1, 2, 3, 4, 9, 10, 11, 12, 13]))
        write_card(tree, "people", "x.md",
                   heading_card(f"- `{CHAT}` message_position 0-13\n"))
        run = cli("work", "waterline", "--records-root", str(tree))
        assert run.returncode == 0, run.stderr
        assert "LABELS_NOT_CONTIGUOUS" in run.stdout
        assert "the records do not choose" in run.stdout
        assert "missing material" not in run.stdout

    def test_the_verb_emits_json_a_caller_can_read(self, tree):
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", positions_messages([0, 4, 9]))
        run = cli("work", "waterline", "--records-root", str(tree), "--json")
        assert run.returncode == 0, run.stderr
        document = json.loads(run.stdout)
        assert document["schema"] == sw.SCHEMA
        assert document["sources"][0]["capture"]["runs"] == [[0, 0], [4, 4], [9, 9]]

    def test_the_verb_refuses_a_relative_records_root(self, tmp_path):
        run = cli("work", "waterline", "--records-root", "records")
        assert run.returncode != 0
        assert "must be absolute" in run.stderr

    def test_reading_the_waterlines_writes_nothing(self, tree):
        """The verb is a reader. The raw half is the collectors' and the card
        half is the cards'; a third writer here would be a second author."""
        write_capture(tree, "people/x/oc_x", chat_manifest())
        write_messages(tree, "people/x/oc_x", positions_messages([0, 1, 2]))
        write_card(tree, "people", "x.md", heading_card(f"- `{CHAT}` message_position 0-1\n"))
        before = {path: path.read_bytes() for path in sorted(tree.rglob("*"))
                  if path.is_file()}
        cli("work", "waterline", "--records-root", str(tree))
        after = {path: path.read_bytes() for path in sorted(tree.rglob("*"))
                 if path.is_file()}
        assert before == after


@pytest.mark.skipif(not readable_dir(LIVE_RECORDS),
                    reason="this node has no records checkout to walk")
class TestTheLiveRecordsRoot:
    def test_the_denominator_is_every_capture_object_and_every_cited_source(self):
        """Not a fixture: the real tree, so a drift here is a drift and not an edit."""
        objects = cc.read_root(LIVE_RECORDS)["objects"]
        document = sw.waterlines(LIVE_RECORDS, ud, objects)
        by_state: dict[str, int] = {}
        for row_ in document["sources"]:
            by_state[row_["state"]] = by_state.get(row_["state"], 0) + 1
        assert sum(by_state.values()) >= len(objects), (by_state, len(objects))
        assert set(by_state) <= set(sw.COMPARISON_STATES), by_state

    def test_the_live_root_reports_something_rather_than_nothing(self):
        run = cli("work", "waterline", "--records-root", str(LIVE_RECORDS))
        assert run.returncode == 0, run.stderr
        assert run.stdout.strip()
