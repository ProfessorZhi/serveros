"""The follow list against the capture tree, and the four ways that join lies.

WHY THIS FILE EXISTS
--------------------
`intake/watch_coverage.py` is new, and every property below is asserted here for
the first time. Read on 2026-09-22, `grep -rn "DRIFT_WATCHLIST\\|drift-sync-
state" --include=*.py` over the fabric tree returned nothing: `drift_sync.py:71
load_watchlist()` reads the list and no ServerOS-side reader had ever opened it.
The four properties that are individually easy to get wrong each get a class:

  * a watch can be silently dead. `drift_sync.locate()` returns None both when a
    link_id matches nothing and when it matches two captures, and its caller logs
    to stderr and **skips**. A reader that resolved the conflict to its first hit
    would report a dead watch as a live one — which is the failure the module
    exists to surface, introduced by the reader itself.
  * the debounce is not a verification. `.drift-sync-state.json` is reset on
    every drift round "无论 commit 是否成功" (`drift_sync.py:246`), so it records
    when a link last *tried to commit*. The live tree holds both values and they
    differ — state `2026-09-15T00:05:53`, manifest `last_verified_at 2026-09-16`.
    A reader that differenced them would invent a contradiction out of two facts
    about two different things, so the test asserts they are reported apart and
    that neither stands in for the other.
  * the collector's fallback is not an authored list. When the file is missing,
    `load_watchlist()` returns `["wiki-litellm-work-doc"]`. A reader that
    printed that as the follow list would say "one document is followed" at the
    exact moment the answer is "the follow list is gone".
  * the reader reads no clock. "Overdue" is a threshold and a threshold is
    authored, so it arrives as `--before`; without one the question is reported
    as not asked. The AST test is the load-bearing one here, because a clock
    read is one line and it would look like a feature.

THE LIVE TREE
-------------
`TestAgainstTheLiveTree` runs against `/data/home/vince/workspace/records` and
asserts the report against the tree rather than against constants alone: the
number of watches must equal the number of link_ids actually in the watchlist
file, read here by a second parser. A fixture that drifted from the tree would
then fail as a drift rather than as an edit.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "runtime" / "cli.py"
MODULE = REPO_ROOT / "intake" / "watch_coverage.py"
from live_checkouts import readable_dir  # noqa: E402

LIVE_RECORDS = Path("/data/home/vince/workspace/records")


def _load():
    spec = importlib.util.spec_from_file_location(
        "serveros_intake_watch_coverage", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wc = _load()


def cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, cwd=str(REPO_ROOT))


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def document_manifest(**over) -> dict:
    """A document capture shaped like the real ones, with only the named fields changed."""
    document = {
        "source_message_id": "om_x",
        "url": "https://example.invalid/wiki/x",
        "type": "wiki",
        "title": "T",
        "fetched_at": "2026-09-15",
        "last_verified_at": "2026-09-16",
        "content_sha256": "a" * 64,
        "prev_content_sha256": "b" * 64,
        "redaction_applied": True,
    }
    document.update(over)
    return document


@pytest.fixture
def tree(tmp_path):
    """A records root a test owns, laid out the way `records/` is.

    The whole root, not just the chats dir: the watchlist and the debounce state
    sit beside it under `information/raw/`, and a fixture that handed the module
    only the chats tree could not exercise either.
    """
    root = tmp_path / "records"
    (root / "information" / "raw" / "feishu" / "chats").mkdir(parents=True)
    return root


def write_watchlist(root: Path, text: str) -> Path:
    path = wc.watchlist_path(root)
    path.write_text(text, encoding="utf-8")
    return path


def write_capture(root: Path, link_id: str, document, chat="people/x/oc_x") -> Path:
    path = wc.chats_root(root) / chat / "links" / link_id / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(document, str):
        path.write_text(document, encoding="utf-8")
    else:
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def write_state(root: Path, state: dict) -> Path:
    path = wc.raw_root(root) / wc.STATE_NAME
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return path


def one_watch(root: Path, link_id="doc-a"):
    return [w for w in wc.read_root(root)["watches"] if w["link_id"] == link_id][0]


# --------------------------------------------------------------------------
# a watch can be silently dead
# --------------------------------------------------------------------------

class TestAWatchCanBeSilentlyDead:
    """`locate()` returns None for zero hits and for two, and its caller skips."""

    def test_one_capture_resolves(self, tree):
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest())
        watch = one_watch(tree)
        assert watch["state"] == "RESOLVED"
        assert watch["title"] == "T"
        assert watch["why"] == ""

    def test_no_capture_is_not_a_resolution(self, tree):
        """The watch can never fire, and that is a finding rather than an empty row."""
        write_watchlist(tree, "doc-a\n")
        watch = one_watch(tree)
        assert watch["state"] == "NO_CAPTURE"
        assert "never fire" in watch["why"]
        assert watch["captures"] == []

    def test_two_captures_are_not_resolved_to_the_first(self, tree):
        """The conflict is the finding. Picking a hit would hide a dead watch."""
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest(), chat="people/x/oc_x")
        write_capture(tree, "doc-a", document_manifest(title="other"),
                      chat="groups/g/oc_g")
        watch = one_watch(tree)
        assert watch["state"] == "AMBIGUOUS"
        assert len(watch["captures"]) == 2
        assert "returns None on a conflict" in watch["why"]
        assert "dead" in watch["why"]
        assert watch["title"] == "" and watch["url"] == ""

    def test_an_unparseable_manifest_is_not_an_absent_capture(self, tree):
        """The capture is there and the collector would skip it — a different fact."""
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", "{not json")
        watch = one_watch(tree)
        assert watch["state"] == "UNREADABLE"
        assert "cannot be parsed" in watch["why"]
        assert len(watch["captures"]) == 1

    def test_a_manifest_that_is_not_an_object_is_unreadable(self, tree):
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", "[1, 2]")
        watch = one_watch(tree)
        assert watch["state"] == "UNREADABLE"
        assert "not a manifest object" in watch["why"]

    def test_a_link_id_matching_another_directory_is_not_a_capture(self, tree):
        """The match is on the parent directory name, which is `locate()`'s own rule."""
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-b", document_manifest())
        assert one_watch(tree)["state"] == "NO_CAPTURE"


# --------------------------------------------------------------------------
# the debounce is not a verification
# --------------------------------------------------------------------------

class TestTheDebounceIsNotAVerification:
    """Two timestamps about two different things, reported apart and never subtracted."""

    def test_the_two_timestamps_are_both_reported(self, tree):
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest(last_verified_at="2026-09-16"))
        write_state(tree, {"doc-a": "2026-09-15T00:05:53"})
        watch = one_watch(tree)
        assert watch["verified"]["at"] == "2026-09-16"
        assert watch["debounce"]["at"] == "2026-09-15T00:05:53"
        assert watch["verified"]["state"] == "RECORDED"
        assert watch["debounce"]["state"] == "RECORDED"

    def test_neither_timestamp_is_derived_from_the_other(self, tree):
        """Only one is present: the other must stay absent rather than echo it."""
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest())
        write_state(tree, {"doc-a": "2026-09-15T00:05:53"})
        manifest_only = one_watch(tree)
        assert manifest_only["verified"]["at"] == "2026-09-16"
        write_state(tree, {})
        state_only = one_watch(tree)
        assert state_only["debounce"]["state"] == "NEVER_COMMITTED"
        assert state_only["verified"]["at"] == "2026-09-16"

    def test_an_unset_gate_is_not_a_gate_that_says_go(self, tree):
        """Absent from the debounce is its own state, with the reason it matters."""
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest())
        watch = one_watch(tree)
        assert watch["debounce"]["state"] == "NEVER_COMMITTED"
        assert "not the same as the gate saying 'go'" in watch["debounce"]["why"]

    def test_a_state_entry_without_a_watch_is_reported_as_residue(self, tree):
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest())
        write_state(tree, {"doc-a": "2026-09-15T00:05:53", "doc-gone": "2026-01-01"})
        report = wc.read_root(tree)
        assert report["state_without_watch"] == ["doc-gone"]
        assert report["summary"]["state_without_watch"] == 1

    def test_a_state_file_that_is_not_an_object_is_malformed(self, tree):
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest())
        (wc.raw_root(tree) / wc.STATE_NAME).write_text("[1]", encoding="utf-8")
        with pytest.raises(wc.Malformed):
            wc.read_root(tree)

    def test_a_never_written_state_file_is_not_an_error(self, tree):
        """The collector writes nothing until a link has been through a round."""
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest())
        report = wc.read_root(tree)
        assert report["state"]["why"] == "never written"
        assert report["summary"]["by_debounce"] == {"NEVER_COMMITTED": 1}


# --------------------------------------------------------------------------
# the collector's fallback is not an authored list
# --------------------------------------------------------------------------

class TestTheFallbackIsNotAnAuthoredList:
    """`load_watchlist()` invents a list when the file is missing. So does nothing here."""

    def test_a_missing_file_is_reported_as_missing(self, tree):
        report = wc.read_root(tree)
        assert report["watchlist"]["listed"] == "COLLECTOR_FALLBACK"
        assert "COLLECTOR'S DEFAULT" in report["watchlist"]["why"]
        assert "not a list anybody authored" in report["watchlist"]["why"]

    def test_the_fallback_still_yields_the_entry_it_would_watch(self, tree):
        """The collector would watch it, so the reader reports it — labelled."""
        report = wc.read_root(tree)
        assert [w["link_id"] for w in report["watches"]] == \
            list(wc.COLLECTOR_BUILTIN_FALLBACK)
        assert report["watches"][0]["listed"] == "COLLECTOR_FALLBACK"

    def test_an_authored_file_is_not_labelled_a_fallback(self, tree):
        write_watchlist(tree, "doc-a\n")
        report = wc.read_root(tree)
        assert report["watchlist"]["listed"] == "AUTHORED"
        assert report["watchlist"]["why"] == ""
        assert report["watches"][0]["listed"] == "AUTHORED"

    def test_the_override_is_honoured(self, tree, tmp_path):
        """A reader that ignored `DRIFT_WATCHLIST` would describe a run that did not happen."""
        other = tmp_path / "elsewhere.txt"
        other.write_text("doc-elsewhere\n", encoding="utf-8")
        report = wc.read_root(tree, watchlist=str(other))
        assert [w["link_id"] for w in report["watches"]] == ["doc-elsewhere"]

    def test_comments_and_blanks_are_skipped_the_way_the_collector_skips_them(self, tree):
        write_watchlist(tree, "# a comment\n\n  doc-a  \n\n# another\ndoc-b\n")
        report = wc.read_root(tree)
        assert [w["link_id"] for w in report["watches"]] == ["doc-a", "doc-b"]
        assert report["watchlist"]["malformed"] == []

    def test_a_line_that_cannot_be_a_link_id_is_counted_not_dropped(self, tree):
        """Dropping it would make the follow list read smaller than it is."""
        write_watchlist(tree, "doc-a\nnot a link id\n")
        report = wc.read_root(tree)
        assert report["watchlist"]["malformed"] == ["not a link id"]
        assert report["summary"]["malformed"] == 1
        assert [w["link_id"] for w in report["watches"]] == ["doc-a"]


# --------------------------------------------------------------------------
# overdue needs an authored cutoff
# --------------------------------------------------------------------------

class TestOverdueNeedsAnAuthoredCutoff:

    def test_without_a_cutoff_the_question_is_not_asked(self, tree):
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest(last_verified_at="2020-01-01"))
        watch = one_watch(tree)
        assert watch["overdue"]["asked"] is False
        assert watch["overdue"]["state"] == "UNDECIDABLE"
        assert "does not read a clock" in watch["overdue"]["why"]

    def test_the_cutoff_decides_by_day(self, tree):
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest(last_verified_at="2026-09-16"))
        assert wc.read_root(tree, before="2026-09-20")["watches"][0]["overdue"]["state"] \
            == "OVERDUE"
        assert wc.read_root(tree, before="2026-09-10")["watches"][0]["overdue"]["state"] \
            == "NOT_OVERDUE"

    def test_the_day_of_the_re_check_is_not_before_that_day(self, tree):
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest(last_verified_at="2026-09-16"))
        assert wc.read_root(tree, before="2026-09-16")["watches"][0]["overdue"]["state"] \
            == "NOT_OVERDUE"

    def test_a_datetime_is_compared_by_its_day(self, tree):
        """A chat capture writes `2026-09-16T15:45:58`; the question is about the day."""
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest(
            last_verified_at="2026-09-16T15:45:58"))
        watch = wc.read_root(tree, before="2026-09-20")["watches"][0]
        assert watch["overdue"]["state"] == "OVERDUE"
        assert "last re-check 2026-09-16" in watch["overdue"]["why"]

    def test_a_capture_with_no_re_check_date_is_undecidable(self, tree):
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest(last_verified_at=None))
        watch = wc.read_root(tree, before="2026-09-20")["watches"][0]
        assert watch["overdue"]["asked"] is True
        assert watch["overdue"]["state"] == "UNDECIDABLE"
        assert "without inventing a date" in watch["overdue"]["why"]

    def test_a_dead_watch_is_undecidable_rather_than_fine(self, tree):
        """NO_CAPTURE has no date, and 'no date' must not read as 'not overdue'."""
        write_watchlist(tree, "doc-a\n")
        watch = wc.read_root(tree, before="2026-09-20")["watches"][0]
        assert watch["state"] == "NO_CAPTURE"
        assert watch["overdue"]["state"] == "UNDECIDABLE"

    def test_a_cutoff_that_is_not_a_day_is_refused(self, tree):
        write_watchlist(tree, "doc-a\n")
        with pytest.raises(wc.Refused):
            wc.read_root(tree, before="last week")
        with pytest.raises(wc.Refused):
            wc.read_root(tree, before="2026-09")

    def test_the_summary_says_whether_the_question_was_asked(self, tree):
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest())
        assert wc.read_root(tree)["summary"]["overdue_asked"] is False
        assert wc.read_root(tree, before="2026-09-20")["summary"]["overdue_asked"] is True
        assert wc.read_root(tree)["summary"]["by_overdue"] == {}


# --------------------------------------------------------------------------
# the reader reads no clock
# --------------------------------------------------------------------------

class TestTheReaderReadsNoClock:
    """The one property that would look like a feature if it were added."""

    def test_the_module_imports_no_clock(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not imported & {"datetime", "time", "calendar", "dateutil"}, (
            f"intake/watch_coverage.py imports {sorted(imported)}. This reader "
            f"answers 'was this re-checked before a day' and the day is authored; "
            f"a clock here would answer 'overdue' against today and look like a "
            f"feature while doing it")

    def test_no_call_in_the_module_names_now_or_today(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        called |= {node.func.id for node in ast.walk(tree)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        assert not called & {"now", "today", "utcnow", "time", "monotonic"}, (
            f"a call to {sorted(called & {'now', 'today', 'utcnow', 'time'})} "
            f"found; the cutoff is the caller's and this reader holds no clock")

    def test_the_cli_verb_supplies_no_default_cutoff(self):
        source = CLI.read_text(encoding="utf-8")
        assert 'info_watched.add_argument(\n        "--before", default="",' in source, (
            "the --before default must be empty: a default here would be this "
            "node's today, which is the clock read the module refuses")

    def test_the_debounce_state_has_no_override(self):
        """The watchlist has one because the collector has one. The state does not."""
        done = cli("information", "watched", "--help")
        assert "--watchlist" in done.stdout
        assert "--state-file" not in done.stdout, (
            "`drift_sync.STATE_FILE` is fixed, so an override here would let a "
            "caller point this reader at a state file the collector never reads "
            "and get a description of a run that never happened")


# --------------------------------------------------------------------------
# every emitted field has a producer
# --------------------------------------------------------------------------

class TestEveryEmittedFieldHasAProducer:

    def test_a_key_with_no_producer_is_a_refusal(self):
        with pytest.raises(wc.Refused):
            wc.no_unsourced({"link_id": "a", "vibe": "good"}, wc.WATCH_PRODUCED, "x")

    def test_the_guard_names_the_offending_key(self):
        with pytest.raises(wc.Refused) as caught:
            wc.no_unsourced({"vibe": "good"}, wc.WATCH_PRODUCED, "the row for 'a'")
        assert "vibe" in str(caught.value)
        assert "the row for 'a'" in str(caught.value)

    def test_every_produced_key_is_actually_emitted(self, tree):
        """The reverse direction: a declared producer with no field is dead weight."""
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest())
        watch = one_watch(tree)
        assert set(watch) == set(wc.WATCH_PRODUCED), (
            f"declared but not emitted: {sorted(set(wc.WATCH_PRODUCED) - set(watch))}; "
            f"emitted but not declared: {sorted(set(watch) - set(wc.WATCH_PRODUCED))}")

    def test_the_row_is_the_only_emitter(self, tree):
        """Every watch row goes through `row()`, so the guard cannot be bypassed."""
        write_watchlist(tree, "doc-a\ndoc-b\n")
        write_capture(tree, "doc-a", document_manifest())
        report = wc.read_root(tree)
        for watch in report["watches"]:
            assert set(watch) == set(wc.WATCH_PRODUCED)


# --------------------------------------------------------------------------
# the counts are beside the answers
# --------------------------------------------------------------------------

class TestTheCountsAreBesideTheAnswers:

    def test_the_denominator_is_the_list_not_the_resolutions(self, tree):
        """A report of one watch over a list of three must not read as three."""
        write_watchlist(tree, "doc-a\ndoc-b\ndoc-c\n")
        write_capture(tree, "doc-a", document_manifest())
        report = wc.read_root(tree)
        assert report["summary"]["denominator"] == 3
        assert report["summary"]["by_state"] == {"RESOLVED": 1, "NO_CAPTURE": 2}

    def test_two_captures_of_one_document_is_not_a_re_check(self, tree):
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest())
        assert wc.read_root(tree)["summary"]["by_verified"] == {"RECORDED": 1}

    def test_a_drift_is_reported_from_the_two_digests(self, tree):
        write_watchlist(tree, "doc-a\ndoc-b\n")
        write_capture(tree, "doc-a", document_manifest(
            content_sha256="a" * 64, prev_content_sha256="b" * 64))
        write_capture(tree, "doc-b", document_manifest(
            content_sha256="c" * 64, prev_content_sha256="c" * 64))
        report = wc.read_root(tree)
        assert report["summary"]["by_drift"] == {"DRIFTED": 1, "NEVER_DRIFTED": 1}
        assert report["watches"][1]["drift"]["why"].startswith(
            "`prev_content_sha256` equals `content_sha256`")

    def test_a_capture_with_no_previous_digest_has_never_drifted(self, tree):
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest(prev_content_sha256=None))
        assert one_watch(tree)["drift"]["state"] == "NEVER_DRIFTED"

    def test_order_is_the_list_order_not_the_filesystem_order(self, tree):
        write_watchlist(tree, "doc-c\ndoc-a\ndoc-b\n")
        report = wc.read_root(tree)
        assert [w["link_id"] for w in report["watches"]] == ["doc-c", "doc-a", "doc-b"]

    def test_every_summary_key_is_a_count_of_something(self, tree):
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest())
        summary = wc.read_root(tree)["summary"]
        assert set(summary) == {
            "denominator", "listed", "by_state", "by_verified", "by_drift",
            "by_debounce", "overdue_asked", "by_overdue", "malformed",
            "state_without_watch"}
        assert summary["denominator"] == sum(summary["by_state"].values())


# --------------------------------------------------------------------------
# the CLI
# --------------------------------------------------------------------------

class TestTheCli:

    def test_a_relative_root_is_refused(self):
        done = cli("information", "watched", "--records-root", "records")
        assert done.returncode != 0
        assert "must be absolute" in done.stderr
        assert "nobody else can reproduce" in done.stderr

    def test_a_root_with_no_capture_tree_is_refused(self, tmp_path):
        done = cli("information", "watched", "--records-root", str(tmp_path))
        assert done.returncode != 0
        assert "no capture root" in done.stderr

    def test_a_bad_cutoff_is_refused(self):
        done = cli("information", "watched", "--records-root", str(LIVE_RECORDS),
                   "--before", "last week")
        assert done.returncode != 0
        assert "must be a YYYY-MM-DD date" in done.stderr

    def test_the_human_lines_and_the_json_are_the_same_run(self, tmp_path):
        tree = tmp_path / "records"
        (tree / "information" / "raw" / "feishu" / "chats").mkdir(parents=True)
        write_watchlist(tree, "doc-a\n")
        write_capture(tree, "doc-a", document_manifest())
        human = cli("information", "watched", "--records-root", str(tree))
        assert human.returncode == 0, human.stderr
        assert "未问——没有给 --before" in human.stdout
        payload = cli("information", "watched", "--json", "--records-root", str(tree))
        assert payload.returncode == 0, payload.stderr
        report = json.loads(payload.stdout)
        assert report["schema"] == wc.SCHEMA
        assert report["summary"]["denominator"] == 1
        assert report["watches"][0]["link_id"] == "doc-a"

    def test_the_human_lines_report_a_dead_watch_as_dead(self, tmp_path):
        tree = tmp_path / "records"
        (tree / "information" / "raw" / "feishu" / "chats").mkdir(parents=True)
        write_watchlist(tree, "doc-a\n")
        done = cli("information", "watched", "--records-root", str(tree))
        assert done.returncode == 0, done.stderr
        assert "NO_CAPTURE" in done.stdout
        assert "never fire" in done.stdout

    def test_the_fallback_is_labelled_in_the_human_lines(self, tmp_path):
        tree = tmp_path / "records"
        (tree / "information" / "raw" / "feishu" / "chats").mkdir(parents=True)
        done = cli("information", "watched", "--records-root", str(tree))
        assert done.returncode == 0, done.stderr
        assert "COLLECTOR'S DEFAULT" in done.stdout

    def test_a_malformed_line_is_shown_not_counted_away(self, tmp_path):
        tree = tmp_path / "records"
        (tree / "information" / "raw" / "feishu" / "chats").mkdir(parents=True)
        write_watchlist(tree, "doc-a\nnot a link id\n")
        done = cli("information", "watched", "--records-root", str(tree))
        assert done.returncode == 0, done.stderr
        assert "不是 link_id 的行" in done.stdout
        assert "not a link id" in done.stdout

    def test_the_verb_is_registered_under_information(self):
        done = cli("information", "--help")
        assert "watched" in done.stdout


# --------------------------------------------------------------------------
# the live tree
# --------------------------------------------------------------------------

class TestAgainstTheLiveTree:
    """The real records root, asserted against the tree and not only against constants."""

    @pytest.fixture(autouse=True)
    def _live(self):
        if not readable_dir(LIVE_RECORDS):
            pytest.skip(f"no live records root at {LIVE_RECORDS}")

    def test_every_watched_link_gets_exactly_one_row(self):
        """The denominator comes from a second parser over the same file."""
        path = wc.watchlist_path(LIVE_RECORDS)
        if not path.exists():
            pytest.skip(f"no live watchlist at {path}")
        authored = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.strip().startswith("#")]
        report = wc.read_root(LIVE_RECORDS)
        assert report["watchlist"]["listed"] == "AUTHORED"
        assert [w["link_id"] for w in report["watches"]] == authored
        assert report["summary"]["denominator"] == len(authored)

    def test_the_live_watch_resolves_to_exactly_one_capture(self):
        report = wc.read_root(LIVE_RECORDS)
        resolved = [w for w in report["watches"] if w["state"] == "RESOLVED"]
        assert resolved, (
            f"no watched link resolved; states are "
            f"{report['summary']['by_state']} — a followed document that cannot "
            f"be located is the finding, not a fixture problem")
        assert report["summary"]["by_state"] == {"RESOLVED": len(resolved)}

    def test_the_live_re_check_date_is_read_as_written(self):
        """Not asserted against a constant: the manifest is re-read here."""
        report = wc.read_root(LIVE_RECORDS)
        watch = report["watches"][0]
        path = wc.captures_for(LIVE_RECORDS, watch["link_id"])[0]
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert watch["verified"]["state"] == "RECORDED"
        assert watch["verified"]["at"] == manifest["last_verified_at"]

    def test_the_live_debounce_is_read_from_the_state_file(self):
        report = wc.read_root(LIVE_RECORDS)
        path = wc.raw_root(LIVE_RECORDS) / wc.STATE_NAME
        if not path.exists():
            pytest.skip(f"no debounce state at {path}")
        state = json.loads(path.read_text(encoding="utf-8"))
        for watch in report["watches"]:
            expected = state.get(watch["link_id"], "")
            assert watch["debounce"]["at"] == (expected if isinstance(expected, str) else "")

    def test_the_two_live_timestamps_are_not_the_same_fact(self):
        """The measurement the module's docstring rests on, asserted on the tree."""
        report = wc.read_root(LIVE_RECORDS)
        both = [w for w in report["watches"]
                if w["debounce"]["at"] and w["verified"]["at"]]
        if not both:
            pytest.skip("no watched link carries both timestamps")
        stated = [w for w in both
                  if w["debounce"]["at"] != w["verified"]["at"]]
        assert stated, (
            "every watched link carries the same debounce and re-check timestamp "
            "on this tree. The module's docstring argues the two files record "
            "different things; if they have converged, that argument needs "
            "re-measuring rather than the assertion relaxing")
