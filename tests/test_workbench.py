#!/usr/bin/env python3
"""§6/§7 — the workbench, and the half of it that can go red on its own.

WHY THIS FILE EXISTS
--------------------
IMPLEMENTATION-PLAN §6 asks for one entry point a person can open, and §7 test 9
asks that the artifact be walked through its real entry point rather than only
from a source checkout. `runtime/workbench.py` answers both by rendering the
document set from commands — which makes the interesting question not "does it
render" but "does the rendered set still describe this node".

The first version of that check compared every document against a fresh render
and went RED on four of them, twice in a row, seconds apart:
STATUS-PREVIEW, ALERT-PREVIEW, RECOVERY-PREVIEW and ENVIRONMENT. The diff was
load average, available memory, and alert/recovery counts. Those pages are
MEASUREMENTS OF A RUNNING SHARED MACHINE. Re-measuring does not reproduce them;
it produces a new reading. So the check as written was wrong — not blunt, wrong
— and the fix was not to loosen a tolerance but to split the set:

  claim   — the page's content comes from commands' VERDICTS and from what a
            person wrote. A fresh render must reproduce it. Compared in full.
  reading — the page's content is a quantity of this node at one moment. A
            fresh render produces a NEW reading; equality is not the criterion.
            What is checked instead: every command the page was rendered FROM
            still answers the way the page says it did.

The tests below are mostly about that split — that it is declared rather than
guessed, that an undeclared document is refused rather than defaulted, and that
each half of the check actually fires on the failure it exists to catch. A check
nobody has watched fail is an assertion, and this file watches both halves fail.

WHY IT DOES NOT RENDER THE WHOLE SET
------------------------------------
A full render re-runs every command every page reads from — measured at ~21s,
and `check` pays it again. A test suite that costs 40 seconds to assert "the
check works" is a test that gets commented out, so the expensive cases take a
SUBSET (`--documents`) and the full-set claim is left where it belongs: in the
round's integration run, against a real install.
"""
from __future__ import annotations

import importlib.util
import io
import contextlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


wb = _load("serveros_workbench_under_test", REPO / "runtime" / "workbench.py")
SRC = wb.SRC


def run_main(argv: list[str]) -> tuple[int, str, str]:
    """`main` returns an exit code and writes to the real streams, so both are
    captured rather than letting a check's DRIFT lines land in the test log as
    if they were failures of the test run."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = wb.main(argv)
    return code, out.getvalue(), err.getvalue()


class EveryDocumentDeclaresItsKind(unittest.TestCase):
    """The classification is the whole design, so it is a checked fact.

    Derived, not guessed, is the rule everywhere else in this tree — but nothing
    in a renderer can tell whether `9.5 GiB` came from a live host or from an
    authored table. So the kind is authored, and what is checked here is that
    authoring it is COMPULSORY: a document with no entry is refused, because
    either default is wrong and the wrong one is silent."""

    def test_the_two_kinds_are_the_only_kinds(self):
        kinds = {kind for kind, _ in SRC.DOC_KINDS.values()}
        self.assertEqual(kinds, {"claim", "reading"})

    def test_every_document_is_classified_with_a_reason(self):
        for name, entry in SRC.DOC_KINDS.items():
            self.assertEqual(len(entry), 2, name)
            self.assertTrue(entry[1].strip(), f"{name} gives no reason")

    def test_the_index_and_the_classification_name_the_same_documents(self):
        """Two lists, one set of names. `DOC_INDEX` is the reader's table of
        contents and `DOC_KINDS` is the checkability classification; a document
        in one and not the other is either unchecked or unlisted, and both are
        silent."""
        indexed = {name for name, _, _ in wb.DOC_INDEX}
        classified = set(SRC.DOC_KINDS)
        self.assertEqual(indexed - classified, set(), "listed but unclassified")
        self.assertEqual(classified - indexed, set(), "classified but unlisted")

    def test_an_unclassified_document_is_refused_not_defaulted(self):
        args = Namespace(projects=["serveros"], project="litellm", item="LIT-0101",
                         quay_run=None, ability_repo=None, release_root=None)
        saved = dict(SRC.DOC_KINDS)
        try:
            SRC.DOC_KINDS.pop("DEPLOYMENT")
            with self.assertRaises(SystemExit) as caught:
                wb.render_all(args, only={"DEPLOYMENT"})
            self.assertIn("DOC_KINDS", str(caught.exception))
            self.assertIn("DEPLOYMENT", str(caught.exception))
        finally:
            SRC.DOC_KINDS.clear()
            SRC.DOC_KINDS.update(saved)


class ClaimsAreComparedInFull(unittest.TestCase):
    """The cheap half: documents that read no commands at all.

    DECISIONS and DEPLOYMENT are authored — a person wrote every line — so a
    fresh render is byte-comparable apart from the clock, and a drifted word is
    a defect with no ambiguity about it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wb-claims-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _render(self):
        code, _, _ = run_main(["--write", "--out", str(self.tmp), "--documents",
                              "DECISIONS", "DEPLOYMENT"])
        self.assertEqual(code, 0)

    def test_render_then_check_is_green(self):
        self._render()
        code, out, _ = run_main(["--check", "--out", str(self.tmp), "--documents",
                                 "DECISIONS", "DEPLOYMENT"])
        self.assertEqual(code, 0, out)
        self.assertIn("2 份主张整页比对", out)

    def test_an_edited_claim_goes_red(self):
        self._render()
        path = self.tmp / "DECISIONS.md"
        text = path.read_text()
        self.assertIn("| D1 | CLOSED |", text)
        path.write_text(text.replace("| D1 | CLOSED |", "| D1 | OPEN |", 1))
        code, _, err = run_main(["--check", "--out", str(self.tmp), "--documents",
                                 "DECISIONS", "DEPLOYMENT"])
        self.assertEqual(code, 1)
        self.assertIn("DRIFT DECISIONS", err)

    def test_a_deleted_document_goes_red(self):
        self._render()
        (self.tmp / "DEPLOYMENT.md").unlink()
        code, _, err = run_main(["--check", "--out", str(self.tmp), "--documents",
                                 "DECISIONS", "DEPLOYMENT"])
        self.assertEqual(code, 1)
        self.assertIn("DEPLOYMENT: 不存在", err)


class ReadingsAreCheckedByTheirCommands(unittest.TestCase):
    """The half that had to be re-designed, exercised on a page with a small
    command set and a trimmed matrix so the test stays affordable.

    START-HERE is a `claim`, but its verdict block is the same mechanism: the
    page records exit codes, and `check` re-runs the commands behind them."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wb-readings-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # Three rows rather than twenty-three. The question is whether a moved
        # exit code is caught, and three rows answer it; twenty-three answer it
        # three seconds slower.
        self.saved_matrix = list(SRC.STATE_MATRIX)
        SRC.STATE_MATRIX[:] = self.saved_matrix[:3]
        self.addCleanup(self._restore)

    def _restore(self):
        SRC.STATE_MATRIX[:] = self.saved_matrix

    def _render_start_here(self):
        code, out, err = run_main(["--write", "--out", str(self.tmp),
                                   "--documents", "START-HERE"])
        self.assertEqual(code, 0, err)
        return (self.tmp / "START-HERE.md").read_text()

    def test_a_flipped_verdict_is_caught_and_the_row_is_named(self):
        text = self._render_start_here()
        first = text.split("<!-- ⟦verdicts⟧\n", 1)[1].splitlines()[0]
        row = first.split("=")[0]
        (self.tmp / "START-HERE.md").write_text(
            text.replace(f"{first}\n", f"{row}=FAILING\n", 1))
        code, _, err = run_main(["--check", "--out", str(self.tmp),
                                 "--documents", "START-HERE"])
        self.assertEqual(code, 1)
        self.assertIn(f"{row}: 盘中 FAILING → 现在", err)

    def test_the_check_reports_what_it_compared_and_what_it_did_not(self):
        self._render_start_here()
        code, out, _ = run_main(["--check", "--out", str(self.tmp),
                                 "--documents", "START-HERE"])
        self.assertEqual(code, 0)
        # The summary must not let "compared" and "not compared" look the same.
        self.assertIn("主张整页比对", out)
        self.assertIn("重跑它们各自记录的来源命令", out)


class TheCheckRefusesRatherThanGuessing(unittest.TestCase):
    """Two situations that are not drift, and must not read as drift.

    An absent directory and fifteen drifted documents produce the same fifteen
    names, and only one of them is a defect. Inside an installed artifact the
    directory is absent ON PURPOSE, so reporting it as fifteen missing files
    would make a fresh install read as a broken workbench."""

    def test_an_absent_directory_is_not_fifteen_missing_documents(self):
        missing = Path(tempfile.mkdtemp(prefix="wb-absent-")) / "never-rendered"
        code, _, err = run_main(["--check", "--out", str(missing),
                                 "--documents", "DECISIONS"])
        self.assertEqual(code, 2)
        self.assertIn("nothing is claimed about it", err)

    def test_render_refuses_to_write_into_an_installed_artifact(self):
        """The guard keys on `<repo>/.git`, because that is what distinguishes a
        checkout (which owns its `workbench/`) from an artifact (whose
        `workbench/` would be a hash-verified revision the next update replaces
        wholesale). Exercised by pointing ROOT at a directory with no .git."""
        saved_root, saved_out = wb.ROOT, wb.OUT_DEFAULT
        tmp = Path(tempfile.mkdtemp(prefix="wb-installed-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        try:
            wb.ROOT = tmp
            wb.OUT_DEFAULT = tmp / "workbench"
            code, _, err = run_main(["--write", "--documents", "DECISIONS"])
            self.assertEqual(code, 2)
            self.assertIn("installed artifact", err)
            self.assertIn("--out", err)
        finally:
            wb.ROOT, wb.OUT_DEFAULT = saved_root, saved_out


class TheVerdictBlockNamesTheRowThatMoved(unittest.TestCase):
    """`3 rows changed` turns three independent findings into one number.

    The verdicts are independent claims about independent verbs, and the row
    that went red is the thing somebody has to go and look at."""

    def test_the_first_changed_row_is_named(self):
        older = "a=OK\nb=OK\nc=OK"
        fresh = "a=OK\nb=FAILING\nc=OK"
        self.assertEqual(wb._first_verdict_change(older, fresh), "b: 盘中 OK → 现在 FAILING")

    def test_a_side_with_no_block_says_so(self):
        self.assertIn("没有判定块", wb._first_verdict_change(None, "a=OK"))

    def test_a_row_added_or_removed_is_reported_as_a_row_count(self):
        self.assertIn("行数不同", wb._first_verdict_change("a=OK", "a=OK\nb=OK"))


class AnswersBlocksParseBackToWhatWasRun(unittest.TestCase):
    """The block is written from the same `RUNLOG` the builder filled, so it
    cannot drift from the page — but only if it round-trips."""

    def test_a_recorded_command_round_trips(self):
        block = wb.answers_block([("serverosctl work board --json", 0),
                                  ("git cat-file -t deadbeef", 128)])
        self.assertEqual(wb._answers_block(block),
                         [("serverosctl work board --json", 0),
                          ("git cat-file -t deadbeef", 128)])

    def test_a_page_that_reads_nothing_says_so(self):
        block = wb.answers_block([])
        self.assertIn("(none)", block)
        self.assertEqual(wb._answers_block(block), [])

    def test_a_command_that_stopped_answering_is_named(self):
        """Driven against a real command whose exit code is recorded wrong, so
        the assertion is about the check and not about a mock of it."""
        moved = wb._answers_moved([("serverosctl work skeleton brief --json", 7)])
        self.assertIn("exit 7", moved)
        self.assertIn("serverosctl work skeleton brief --json", moved)

    def test_a_command_that_still_answers_is_not_a_defect(self):
        self.assertEqual(wb._answers_moved([("serverosctl work skeleton brief --json", 0)]), "")


def _stdout_of(argv: list[str]) -> str:
    """One document, rendered to a string instead of to `workbench/`."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        wb.main(["--stdout", *argv])
    return out.getvalue()


class TheCommittedSetNamesNoNodeAbsolutePath(unittest.TestCase):
    """`workbench/` is committed, so one node's home must not be spelled out in it.

    This is the artifact's AC-004 rule applied to the other published surface.
    That criterion greps the artifact's `runtime/` and `bin/` and passes; the
    reviewed snapshot under `workbench/` was carrying `/data/home/vince/...` in
    every one of its fifteen documents, which is the same leak one directory
    over. The fix is a display transform at the render收口, and the point of
    asserting it HERE is that a leak reappears the moment somebody adds a
    document that formats a path itself."""

    def test_the_transform_replaces_the_home_prefix(self):
        home = str(Path.home())
        self.assertEqual(wb.rel_home(f"see {home}/workspace/records"),
                         "see ~/workspace/records")
        self.assertEqual(wb.rel_home(f"root is {home}"), "root is ~")
        self.assertEqual(wb.rel_home("/etc/systemd/system"), "/etc/systemd/system")

    def test_a_rendered_document_carries_no_home_prefix(self):
        """The real assertion, over a real render — not the unit above re-stated.

        RELEASE-MANIFEST is the document most likely to name a path, because the
        pins ARE paths, and `--release-root` makes it print one more."""
        rendered = _stdout_of(["RELEASE-MANIFEST", "--release-root", str(REPO / "runtime")])
        self.assertNotIn(str(Path.home()), rendered)
        self.assertIn("~/", rendered, "the transform removed the paths rather than "
                                      "shortening them, which is a different defect")


class ReachabilityMeansAncestryNotEquality(unittest.TestCase):
    """§6 asks whether every pin is REACHABLE. The column used to print the
    branch's head and call a moved head a divergence, which made a `claim` page
    — compared in full — go red on every commit for a reason that was not a
    defect. The predicate is `merge-base --is-ancestor`, and this watches all
    three of its answers on a real repository rather than asserting them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.repo = base / "repo"
        self.repo.mkdir()
        self.remote = base / "origin.git"
        git = lambda *a, cwd=None: subprocess.run(  # noqa: E731
            ["git", *a], cwd=str(cwd or self.repo), capture_output=True, text=True)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@example.invalid")
        git("config", "user.name", "t")
        (self.repo / "f").write_text("1")
        git("add", "f")
        git("commit", "-q", "-m", "first")
        self.on_branch = git("rev-parse", "HEAD").stdout.strip()
        (self.repo / "f").write_text("2")
        git("commit", "-qam", "second")
        self.tip = git("rev-parse", "HEAD").stdout.strip()
        git("init", "-q", "--bare", str(self.remote))
        git("remote", "add", "origin", str(self.remote))
        git("push", "-q", "origin", "main")
        # A revision that is NOT on the pushed branch: committed, then reset away.
        git("checkout", "-q", "-b", "side", self.on_branch)
        (self.repo / "g").write_text("x")
        git("add", "g")
        git("commit", "-q", "-m", "unpushed")
        self.off_branch = git("rev-parse", "HEAD").stdout.strip()
        git("checkout", "-q", "main")

    def tearDown(self):
        self.tmp.cleanup()

    def _row(self, revision):
        return wb._reachable({"name": "n", "repo": str(self.repo),
                              "revision": revision, "branch": "main"})

    def test_a_revision_the_branch_contains_reads_reachable(self):
        """Even though the head has moved past it — which is the whole point."""
        self.assertIn("⊇ pin", self._row(self.on_branch)[4])

    def test_a_revision_the_branch_does_not_contain_is_a_failure(self):
        self.assertIn("不含 pin", self._row(self.off_branch)[4])

    def test_a_branch_that_is_not_on_the_remote_is_a_failure(self):
        row = wb._reachable({"name": "n", "repo": str(self.repo),
                             "revision": self.on_branch, "branch": "no-such-branch"})
        self.assertIn("远端不存在", row[4])

    def test_the_column_carries_no_head_sha(self):
        """The MOVING head is what made the page flap; it must not come back.

        The pin's own revision is printed — that is the column's subject. What
        must not be printed is the branch tip, because it changes on every commit
        and this page is compared in full."""
        row = self._row(self.on_branch)
        joined = " ".join(row)
        self.assertNotIn(self.tip[:12], joined)
        self.assertIn(self.on_branch[:12], joined)


class TheReportsSectionReadsTheDirectoryItIsGiven(unittest.TestCase):
    """§6 asks for 日报 on this page, and a run's reports live in the run's own
    directory — which is never a default, for the reason every other input here
    is not: a default naming one round's scratch tree renders the section out of
    whatever happens to still be on this disk, on this node only. So the section
    has two states and both are checked: pointed at a directory holding a report
    it names the file, and pointed at nothing it says it found nothing rather
    than rendering an empty list that reads as "there were none"."""

    def test_a_report_in_the_given_directory_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "daily-report-20260920.md"
            report.write_text("# 日报\n\n今天做了什么。\n", encoding="utf-8")
            lines = wb._reports(None, [tmp])
            self.assertEqual(len(lines), 1)
            self.assertIn("daily-report-20260920.md", lines[0])
            self.assertIn(str(report.stat().st_size), lines[0])

    def test_nothing_found_says_so_instead_of_listing_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            lines = wb._reports(None, [tmp])
            self.assertTrue(any("没有找到任何日报文件" in line for line in lines))
            self.assertTrue(any("不是绿的" in line for line in lines))

    def test_a_missing_directory_is_not_an_exception(self):
        """`--reports-dir` naming a directory that is not there is a normal
        input on a node that has not run that round, not a crash."""
        lines = wb._reports(None, ["/nonexistent/reports/for/this/node"])
        self.assertTrue(any("没有找到任何日报文件" in line for line in lines))

    def test_the_fallback_names_no_round(self):
        """The shipped renderer must not carry one round's filename.

        It did: the fallback pointed at `RUN-20260920-foundation-skeleton-e/
        daily-report-quay.md`, which is wrong on every other node and after
        every other round — a stale string that reads as help. The `RUN-` token
        is the tell, and this is the guard that keeps it out."""
        lines = wb._reports(None, ["/nonexistent/reports/for/this/node"])
        joined = "\n".join(lines)
        self.assertNotIn("RUN-", joined)
        self.assertIn("--reports-dir", joined, "the fallback must say how to fix it")


class TheColophonSaysWhatWasActuallyChecked(unittest.TestCase):
    """A page whose own header overstates its check is the one thing a reader
    cannot catch from the page.

    Every `reading` page printed "只核对这些命令今天仍然回答" — including the two
    that record no commands at all. On those the check compares section headings
    and nothing else, so the sentence named a verification that did not happen.
    The fix is to derive the clause from what the page actually recorded."""

    def test_a_reading_with_no_recorded_command_says_so(self):
        note = wb.kind_note("QUAY-RESULTS", [])
        self.assertIn("读数", note)
        self.assertIn("没有记录可重跑的命令", note)

    def test_a_reading_with_recorded_commands_does_not_say_it(self):
        note = wb.kind_note("ENVIRONMENT", [("serverosctl device show --json", 0)])
        self.assertIn("读数", note)
        self.assertNotIn("没有记录可重跑的命令", note)

    def test_the_two_pages_this_is_about_are_the_ones_without_commands(self):
        """Which pages have no recorded command is a fact about the renderer, so
        it is measured rather than listed: a page that starts recording commands
        stops needing the clause, and this assertion then fails rather than
        quietly covering nothing."""
        for name in ("QUAY-RESULTS", "REPORTS"):
            block = wb._answers_block(_stdout_of(
                [name, "--quay-run", str(REPO), "--out", "workbench"]))
            self.assertEqual(
                block, [],
                f"{name} now records commands, so it no longer needs the clause "
                f"that says it records none — and the colophon text should change "
                f"with it")


if __name__ == "__main__":
    unittest.main()
