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
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from unittest import mock
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


class TheSnapshotHeaderDoesNotSurviveNormalisation(unittest.TestCase):
    """`--check` strips clock-derived values from both sides before comparing.
    The snapshot header is the line that CARRIES the clock, so it is the line
    that has to go.

    It did not. The VOLATILE pass rewrites the timestamp to `<volatile>` first,
    and the pattern that removed the header was `<!-- ⟦snapshot⟧[^>]*-->` — a
    character class that cannot cross the `>` of the `<volatile>` it had just
    produced, so `-->` was never reached and the substitution matched nothing at
    all. Measured on a rendered START-HERE: the header came through whole, its
    worktree path and its inputs included, inside the one function whose entire
    job is to make two renders comparable.

    The tests below are the two consequences rather than the pattern: the header
    is gone, and two checkouts of the same page normalise the same.
    """

    BODY = "# START-HERE\n\nthe page\n"

    @staticmethod
    def header(when="2026-09-20T11:02:13+00:00",
               checkout="~/workspace/worktrees/serveros/SRV-0011"):
        return (f"<!-- ⟦snapshot⟧ rendered {when} by `runtime/workbench.py` from "
                f"{checkout} · inputs: project=litellm; item=LIT-0101 -->")

    def test_the_header_is_gone_after_normalising(self):
        out = wb.normalize(self.header() + "\n" + self.BODY)
        self.assertNotIn("⟦snapshot⟧", out)
        self.assertNotIn("worktrees", out)
        self.assertEqual(out.strip(), self.BODY.strip())

    def test_two_checkouts_of_the_same_page_normalise_the_same(self):
        """The false DRIFT. An unchanged set rendered in a second worktree
        differs only in the header's path, and a check that compares the header
        reports every page in the set as drifted — for a reason no page is
        about."""
        a = self.header(checkout="~/workspace/worktrees/serveros/SRV-0011")
        b = self.header(checkout="~/workspace/worktrees/serveros/SRV-0011-copy")
        self.assertEqual(wb.normalize(a + "\n" + self.BODY),
                         wb.normalize(b + "\n" + self.BODY))

    def test_an_unterminated_header_does_not_swallow_the_page(self):
        """Why the pattern may not become `.*?` under DOTALL. A header with no
        `-->` has to leave its own line standing rather than eat every line up
        to the next `-->` in the document — which is what this page has."""
        text = ("<!-- ⟦snapshot⟧ rendered 2026-09-20T11:02:13+00:00\n"
                + self.BODY + "\n<!-- a later comment -->\n")
        self.assertIn("# START-HERE", wb.normalize(text))


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


class ARowsExpectationIsCheckedAgainstItsOwnCommand(unittest.TestCase):
    """A row's `expect` is the row's whole content, and only `check` reads it.

    `check` compares the rendered set against a fresh render, so what it catches
    is a row that MOVED. It cannot catch a row whose declared expectation was
    wrong from the start: the document says FAILING, the re-run says FAILING, and
    the two agree with each other about a claim that is simply false. Measured,
    not reasoned about — flipping the catalogue row's `expect` from 2 to 1
    changes no byte length, reddens no test in this file, and leaves `check`
    exiting 0 with a FAILING row inside a document it calls consistent.

    So the rows this round added are pinned to their commands here. The set is
    derived from the ids rather than listed, so a third `ability-resolve` row
    would be covered by having been added rather than by being remembered.

    Two rows, not twenty-four: the file's docstring says why the expensive cases
    take a subset, and this class is about the mechanism — that a declared
    expectation is re-run — not about the state of all twenty-four rows, which is
    what the rendered START-HERE is for and what the round's integration run
    reads."""

    def test_the_ability_resolve_rows_return_the_code_they_declare(self):
        rows = [r for r in SRC.STATE_MATRIX
                if str(r.get("id", "")).startswith("ability-resolve")]
        self.assertTrue(rows, "the matrix carries no ability-resolve row at all")
        for row in rows:
            code, good, err, _ = wb.row_verdict(row)
            self.assertTrue(
                good,
                f"{row['id']} declares expect={row['expect']!r} and got exit {code}"
                + (f", stderr={err!r}" if err else "")
                + " — the row is a published claim and it is false")


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

    def test_a_row_inserted_in_the_middle_is_named_as_added(self):
        """The shape the append-only test above could not reach, and the shape
        this round actually produced: START-HERE's verdict block gained
        `ability-resolve-catalogue` in the middle, and the position-paired
        comparison reported `registration-discover: 盘中 OK → 现在 OK` — a row
        that had not moved, named with two identical verdicts."""
        older, fresh = "a=OK\nregistration-discover=OK", \
            "a=OK\nability-resolve-catalogue=OK\nregistration-discover=OK"
        change = wb._first_verdict_change(older, fresh)
        self.assertIn("行数不同（2 → 3）", change)
        self.assertIn("新增 ability-resolve-catalogue", change)
        self.assertNotIn("registration-discover:", change)

    def test_a_row_removed_from_the_middle_is_named_as_removed(self):
        change = wb._first_verdict_change("a=OK\nb=OK\nc=OK", "a=OK\nc=OK")
        self.assertIn("行数不同（3 → 2）", change)
        self.assertIn("移除 b", change)
        self.assertNotIn("c:", change)


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


class TheTwoColumnsAskTwoQuestions(unittest.TestCase):
    """§6 asks whether every pin is REACHABLE. One column used to answer, and it
    asked a different question: *does a ref with the name typed into
    `workbench_sources.PINS` exist on the remote*. It was wrong in both
    directions, and both were seen on this round's real inputs — a revision that
    WAS published, whose pin named a ref nobody had created, read as a dangling
    pin; and a ref created after the fact, pointing at anything at all, turned
    the page green without making the pin one bit more obtainable.

    So there are two columns, and neither implies the other.
    `revision_remote_reachable` is a property of the REVISION, asked against the
    refs the remote advertises. `designated_release_ref` is a property of the
    DECLARATION — which ref this pin says it publishes at, and whether that ref
    carries the revision. This class watches each answer its own question on a
    real repository, the two directions of the old false verdict among them,
    plus the case where the remote does not answer at all — which is neither
    可达 nor 不可达."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.repo = base / "repo"
        self.repo.mkdir()
        self.remote = base / "origin.git"
        git = lambda *a, cwd=None: subprocess.run(  # noqa: E731
            ["git", *a], cwd=str(cwd or self.repo), capture_output=True, text=True)
        self.git = git
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
        # A revision no advertised ref carries: committed, then left behind.
        git("checkout", "-q", "-b", "side", self.on_branch)
        (self.repo / "g").write_text("x")
        git("add", "g")
        git("commit", "-q", "-m", "unpushed")
        self.off_branch = git("rev-parse", "HEAD").stdout.strip()
        git("checkout", "-q", "main")

    def tearDown(self):
        self.tmp.cleanup()

    def _row(self, revision, release_ref=None, publishes=None):
        return wb._reachable({"name": "n", "repo": str(self.repo),
                              "revision": revision, "release_ref": release_ref,
                              "publishes": publishes})

    # ── column 1 · is this REVISION obtainable from the remote at all ─────────
    #
    # `可达` is a substring of `不可达`, so the reachable case is written with
    # assertEqual and never with assertIn: an assertIn here would pass on the
    # very value it exists to exclude.

    def test_a_revision_an_advertised_ref_carries_is_reachable(self):
        """Even though the head has moved past it — which is the whole point."""
        self.assertEqual("可达", self._row(self.on_branch, "main")[4])

    def test_a_revision_no_advertised_ref_carries_is_unreachable(self):
        row = self._row(self.off_branch, "main")
        self.assertIn("不可达", row[4])
        self.assertIn("没有任何 ref 包含它", row[4])

    def test_a_revision_this_clone_does_not_have_is_not_called_reachable(self):
        """A false green found while writing this repair, on this node.

        `git rev-list --ignore-missing <sha>` exits 0 with EMPTY OUTPUT for an
        object the clone does not have — it means "skip what you cannot open",
        and empty output is what this column reads as 可达. The flag looked like
        tolerance and would have printed `本地对象=不存在` beside
        `revision_remote_reachable=可达` inside one row. The command is now run
        without it, and it refuses instead."""
        row = self._row("deadbeef" * 5, "main")
        self.assertIn("不存在", row[3])
        self.assertTrue(row[4].startswith("**未判定**"), row[4])
        self.assertNotEqual("可达", row[4])

    def test_a_remote_that_does_not_answer_is_not_unreachable(self):
        """A source that says "no such thing" is an answer; a source that did
        not answer is a gap. Rendering them alike is how a broken remote becomes
        a dangling pin — and 未答 must never be printed as 不可达."""
        self.git("remote", "set-url", "origin",
                 str(Path(self.tmp.name) / "no-such-remote"))
        row = self._row(self.on_branch, "main")
        # The verdict is the cell's LEADING token. It is not asserted by
        # substring absence: this cell deliberately names 不可达 in its prose —
        # "不是「不可达」" — which is the page explaining the distinction a
        # person needs, and a test demanding the word be absent would be asking
        # the page to stop explaining itself.
        self.assertTrue(row[4].startswith("**远端未答**"), row[4])
        self.assertTrue(row[5].startswith("`main` **远端未答**"), row[5])

    def test_a_remote_whose_refs_we_do_not_hold_is_undecided(self):
        """The remote answered, and answered about objects this clone does not
        have — so the ancestry question cannot be asked at all. That is 未判定,
        and it is not 不可达 either: it is a third state and it says so."""
        other = Path(self.tmp.name) / "other"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(other)],
                       capture_output=True, text=True)
        for k, v in (("user.email", "o@example.invalid"), ("user.name", "o")):
            self.git("config", k, v, cwd=other)
        (other / "h").write_text("y")
        self.git("add", "h", cwd=other)
        self.git("commit", "-qam", "moved on", cwd=other)
        self.git("push", "-q", "origin", "main", cwd=other)
        self.assertTrue(self._row(self.on_branch, "main")[4].startswith("**未判定**"))

    # ── column 2 · what this pin SAYS it publishes at ────────────────────────

    def test_a_revision_published_behind_an_uncreated_ref_is_still_reachable(self):
        """The false red this repair removes. `release_ref` names a ref nobody
        created; the revision is on `main` and column 1 says so. Column 2
        reports its own finding — 未发布 — and does not overwrite column 1."""
        row = self._row(self.on_branch, "no-such-ref")
        self.assertEqual("可达", row[4])
        self.assertIn("未发布", row[5])

    def test_a_ref_created_after_the_fact_changes_only_column_two(self):
        """The repair's own control.

        A ref pushed later, carrying only what it carries, must move column 2
        and must NOT move column 1. This is the assertion that a 补同名 branch
        cannot manufacture a 悬空 pin 已修复: the ref exists in both rows below,
        and the two rows disagree about reachability because the revisions are
        different — not because a name was created."""
        self.git("push", "-q", "origin", f"{self.tip}:refs/heads/late-line")
        unreachable = self._row(self.off_branch, "late-line")
        self.assertIn("不可达", unreachable[4])
        self.assertIn("不含 pin", unreachable[5])
        reachable = self._row(self.on_branch, "late-line")
        self.assertEqual("可达", reachable[4])
        self.assertIn("⊇ pin", reachable[5])

    def test_a_pin_that_declares_no_publication_point_is_a_gap(self):
        """An undeclared publication point is a missing declaration, not a
        failed one, and the two must not print alike."""
        row = self._row(self.on_branch)
        self.assertEqual("可达", row[4])
        self.assertIn("未指定", row[5])

    def test_a_pin_held_without_claiming_publication_says_so(self):
        """`publishes=False` is a DECISION and it is distinguishable from having
        said nothing — which is the whole reason the flag exists."""
        self.assertIn("不声称发布", self._row(self.on_branch, publishes=False)[5])

    def test_the_columns_carry_no_head_sha(self):
        """The MOVING head is what made the page flap; it must not come back.

        The pin's own revision is printed — that is the row's subject. What must
        not be printed is a ref tip, because it changes on every commit and this
        page is compared in full."""
        row = self._row(self.on_branch, "main")
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


class TheCheckRefusesInputsTheSetWasNotRenderedWith(unittest.TestCase):
    """A set rendered WITH `--quay-run` carries sections a render without it
    reports as 「没有答案」.

    Comparing those two reports drift on pages that are individually correct,
    and the verdict names the wrong cause — the failure class this whole round
    keeps recording. So the inputs are compared before any page is, and a
    mismatch is stated as a fact about the CHECK rather than about the
    documents.
    """

    def _set(self, tmp: str, **extra) -> Path:
        out = Path(tmp) / "set"
        code, _, err = run_main(
            ["--out", str(out), "--write", "--documents", "TODAY",
             *sum(([f"--{k.replace('_', '-')}", v]
                   for k, v in extra.items()), [])])
        self.assertEqual(code, 0, err)
        return out

    def test_a_set_records_the_inputs_it_was_rendered_with(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._set(tmp, project="litellm")
            head = (out / "TODAY.md").read_text().split("\n", 1)[0]
            self.assertIn("inputs:", head)
            self.assertIn("project=litellm", head)

    def test_check_with_other_inputs_names_the_key_that_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._set(tmp, project="litellm")
            code, _, err = run_main(["--out", str(out), "--check", "--documents",
                                     "TODAY", "--project", "serveros"])
            self.assertEqual(code, 2)
            self.assertIn("project", err)
            # The verdict a person must NOT read: pages reported as drifted when
            # the real cause is the invocation.
            self.assertNotIn("DRIFT", err)

    def test_a_set_records_no_inputs_is_refused_not_matched(self):
        """An older set that does not say what it was rendered with cannot be
        checked — but "we do not know" must not read as "it matched"."""
        with tempfile.TemporaryDirectory() as tmp:
            out = self._set(tmp, project="litellm")
            path = out / "TODAY.md"
            text = path.read_text()
            head, _, body = text.partition("\n")
            path.write_text(
                "<!-- ⟦snapshot⟧ rendered 2026-01-01T00:00:00+00:00 by "
                "`runtime/workbench.py` from /somewhere -->\n" + body)
            self.assertNotEqual(head, "")
            code, _, err = run_main(["--out", str(out), "--check", "--documents",
                                     "TODAY", "--project", "litellm"])
            self.assertEqual(code, 2)
            self.assertIn("更早的格式", err)
            self.assertNotIn("DRIFT", err)

    def test_inputs_differ_names_only_what_differs(self):
        a = "projects=x; project=a; item=c"
        b = "projects=x; project=b; item=c"
        self.assertEqual(wb.inputs_differ(a, b), ["project"])
        self.assertEqual(wb.inputs_differ(a, a), [])

    def test_a_key_this_revision_does_not_know_is_a_difference(self):
        """A set rendered by a newer revision may carry an input this one has no
        flag for. Dropping it would compare two sets and report them equal."""
        self.assertIn("future-flag",
                      wb.inputs_differ("project=a", "project=a; future-flag=z"))

    def test_recorded_inputs_reads_the_header_and_not_the_body(self):
        """The pages discuss their own inputs in prose. A body mentioning
        `inputs:` must not be read as the record."""
        self.assertIsNone(wb.recorded_inputs(
            "<!-- ⟦snapshot⟧ rendered x from / -->\n\n# TODAY\n\n我们把 inputs: "
            "写在这里会被误读。\n"))
        self.assertEqual(
            wb.recorded_inputs("<!-- ⟦snapshot⟧ rendered x · inputs: project=a -->\nbody"),
            "project=a")


class AnUnreadableFileIsASectionNotATraceback(unittest.TestCase):
    """`--quay-run` is a directory somebody points this at, and on a shared host
    a plain directory holds other actors' files.

    An unreadable log used to raise out of the renderer and leave all fifteen
    documents unwritten — a tenth of the set deciding the fate of the whole. The
    page must say it could not read the file, on that page, and the rest of the
    set must still be produced.
    """

    def test_an_unreadable_log_is_named_on_the_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "another-actors.log"
            secret.write_text("someone else's run\n")
            secret.chmod(0o000)
            if secret.exists() and secret.stat().st_size and _readable(secret):
                self.skipTest("running as a user who can read mode-000 files")
            code, out, err = run_main(
                ["--stdout", "QUAY-RESULTS", "--quay-run", tmp])
            self.assertEqual(code, 0, err)
            self.assertIn("运行日志 another-actors.log", out)
            self.assertIn("读不了", out)
            self.assertIn("不是空", out)
            secret.chmod(0o600)

    def test_the_rest_of_the_set_is_still_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "another-actors.log"
            secret.write_text("someone else's run\n")
            secret.chmod(0o000)
            if _readable(secret):
                self.skipTest("running as a user who can read mode-000 files")
            out = Path(tmp) / "set"
            code, _, err = run_main(["--out", str(out), "--write",
                                     "--quay-run", tmp])
            self.assertEqual(code, 0, err)
            self.assertEqual(len(list(out.glob("*.md"))), 15)
            secret.chmod(0o600)


class TheQuayBinaryIsSuppliedNotBakedIn(unittest.TestCase):
    """The Quay CLI's location is a fact about the node, not about the repo.

    The first version listed two absolute paths under one home directory. AC-004
    does not catch that: its criterion is "产物的 `runtime/` 与 `bin/` 里没有出现
    **构建它的**工作树路径", which is the ServerOS build worktree, and a Quay
    worktree is a different path. The rule AC-004 is an instance of is the one
    asserted here, on the shipped file.
    """

    def test_the_module_carries_no_literal_home_path(self):
        text = (REPO / "runtime" / "workbench.py").read_text()
        self.assertNotIn(
            str(Path.home()), text,
            "runtime/workbench.py names this node's home as a literal — that is "
            "the defect --quay-bin exists to remove, in the surface that ships")

    def test_the_binary_comes_from_the_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "quay.js"
            fake.write_text("// not the real one\n")
            self.assertEqual(wb._quay_binary(str(fake)), fake)

    def test_the_binary_comes_from_the_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "quay.js"
            fake.write_text("// not the real one\n")
            with mock.patch.dict(os.environ, {"SERVEROS_QUAY_BIN": str(fake)}):
                self.assertEqual(wb._quay_binary(), fake)

    def test_neither_means_none_rather_than_a_guess(self):
        """A search that fell back to a plausible path would answer on this node
        and silently render 「没有答案」 on every other one."""
        with mock.patch.dict(os.environ, {"SERVEROS_QUAY_BIN": ""}):
            self.assertIsNone(wb._quay_binary())

    def test_the_page_names_the_flag_that_would_fill_it(self):
        with mock.patch.dict(os.environ, {"SERVEROS_QUAY_BIN": ""}):
            code, out, err = run_main(
                ["--stdout", "QUAY-RESULTS", "--quay-run", str(REPO)])
        self.assertEqual(code, 0, err)
        self.assertIn("--quay-bin", out)
        self.assertIn("不是空", out)

    def test_a_rendered_page_carries_no_literal_home_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "quay.js"
            fake.write_text("// not the real one\n")
            with mock.patch.dict(os.environ, {"SERVEROS_QUAY_BIN": ""}):
                out = _stdout_of(["QUAY-RESULTS", "--quay-run", str(REPO),
                                  "--quay-bin", str(fake)])
            self.assertNotIn(str(Path.home()), out)


def _readable(path: Path) -> bool:
    try:
        path.read_text()
        return True
    except OSError:
        return False


if __name__ == "__main__":
    unittest.main()
