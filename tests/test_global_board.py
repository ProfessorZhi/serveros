#!/usr/bin/env python3
"""§S — the Global Requirement Board, and the three nouns it must not blend.

WHY THIS FILE EXISTS
--------------------
§S asks for three things that are easy to write as one and wrong that way:
Requirement Source, Work Item, and Requirement Board. The board is the only
one of the three this repository builds, and the two ways it can be wrong are
both about claiming to be the other two:

  * it can read the work-engine store, becoming a second authority on what
    state an Item is in;
  * it can write back — a completion flag, a priority, a column — becoming a
    store whose contents somebody has to reconcile with the real one.

Nothing here reads the store (the boundary is `fleet/mcp-contract.yaml`, and
the input is the board documents the control plane already rendered from it),
and `render()` only ever writes the projection. Several tests below are about
the ways a projection stops being one.

THE ONE TEST THAT IS NOT ABOUT THIS MODULE
------------------------------------------
`test_the_completion_states_are_the_authoritys`. `COMPLETED_STATES` is not a
taste; it is `state_machine.reopen()`, which is the one place in this fabric
that says what "finished" means. The test parses that function's source and
compares the set it accepts, so that the day the authority gains a fourth
state this board fails instead of quietly filing Items under the wrong
heading. If the authority moves, this test tells you the board is stale — it
does not tell you the board is wrong, and that difference is the point.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


board = _load("serveros_global_board", REPO / "intake" / "global_board.py")
paths_mod = _load("serveros_runtime_paths", REPO / "runtime" / "paths.py")

# The authority's checkout, located by the ROOT CONTRACT and not by counting
# `..` from this file. `REPO.parent.parent / "src"` is right in a canonical
# checkout and wrong in every worktree — this file runs from
# `worktrees/serveros/<item>/`, one level deeper than that arithmetic assumes,
# and the first version of this file computed it that way and SKIPPED. A skip
# is the worst outcome available here: the test that checks the board's
# completion states against the authority's would have reported "cannot check"
# forever while reading as a deliberate, satisfied condition.
WORK_ENGINE = (paths_mod.Paths().root("workspace") / "src" / "work-engine"
               / "workengine")


def a_board(project: str, items: list[dict]) -> dict:
    return {"schema": "serveros/board.v3", "project": project,
            "generated_at": "2026-01-01T00:00:00+00:00",
            "generation_source_revision": None, "items": items}


def an_item(item_id: str, **overrides) -> dict:
    item = {"id": item_id, "title": f"item {item_id}", "kind": "delivery",
            "state": "ACTIVE", "card_locator": f"records/{item_id}/card.yaml",
            "activities": [], "deliverables": []}
    item.update(overrides)
    return item


# ---------------------------------------------------------------------------
# The completion set comes from the authority, not from here.
# ---------------------------------------------------------------------------


class TheCompletionStatesAreDerived(unittest.TestCase):
    def test_the_completion_states_are_the_authoritys(self):
        """`reopen()` accepts exactly the states this board calls finished.

        Parsed from the source rather than imported, because importing
        work-engine would make this repository depend on a checkout that need
        not be present for the fabric to run. If the checkout is absent the
        test SKIPS and says so — a silent pass here would be a board claiming
        a derivation it did not make.
        """
        state_machine = WORK_ENGINE / "state_machine.py"
        if not state_machine.is_file():
            self.skipTest(
                f"{state_machine} is not on this node, so the derivation "
                f"cannot be checked here. The board's set is "
                f"{board.COMPLETED_STATES} and it is reported as UNVERIFIED "
                f"rather than confirmed.")
        tree = ast.parse(state_machine.read_text(encoding="utf-8"))
        accepted = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "reopen":
                for inner in ast.walk(node):
                    # `if current not in {CANCELLED, CLOSED}:` — the set the
                    # function accepts, however it is spelled.
                    if (isinstance(inner, ast.Compare)
                            and isinstance(inner.comparators[0], ast.Set)):
                        accepted = {e.attr for e in inner.comparators[0].elts}
        self.assertIsNotNone(
            accepted, "reopen() no longer compares against a literal set of "
                      "states; this test can no longer derive the answer and "
                      "must be rewritten rather than deleted")
        self.assertEqual(set(board.COMPLETED_STATES), accepted,
                         "the states this board files under 已完成 are no "
                         "longer the states the authority treats as finished "
                         "and reopenable")

    def test_delivered_is_not_completed(self):
        """DELIVERED sits between ACTIVE and CLOSED and still has work left.

        The tempting version of this board completed anything with an
        `outcome`, which would have struck through and filed away Items whose
        Run had delivered and whose Item had not closed.
        """
        self.assertNotIn("DELIVERED", board.COMPLETED_STATES)
        document = board.compose([a_board("p", [an_item("X", state="DELIVERED")])])
        self.assertEqual(document["counts"]["active"], 1)
        self.assertEqual(document["counts"]["completed"], 0)


# ---------------------------------------------------------------------------
# The joins. Each is optional in the world, and each absence is a value.
# ---------------------------------------------------------------------------


class TheJoins(unittest.TestCase):
    def rows(self, **kwargs):
        return board.compose([a_board("p", [an_item("X", **kwargs)])])["items"]

    def test_no_card_at_all_is_not_found(self):
        row = self.rows()[0]
        self.assertEqual(row["source"]["state"], "NOT_FOUND")
        self.assertEqual(row["source"]["source_refs"], [])
        self.assertIsNone(row["execution_session"])

    def test_a_card_that_does_not_parse_is_unreadable_not_not_found(self):
        """The correction. Two values here sent readers to the wrong place.

        An unparseable card became `None`, `None` meant NOT_FOUND, and a YAML
        error in a file sitting on disk was reported as a file that is not
        there. `card_known` is what separates them and this is the test that
        keeps it doing so.
        """
        rows = self.rows()
        self.assertEqual(
            board.item_row(rows[0], project="p", card=None, card_known=False,
                           binding=None)["source"]["state"], "NOT_FOUND")
        self.assertEqual(
            board.item_row(rows[0], project="p", card=None, card_known=True,
                           binding=None)["source"]["state"], "UNREADABLE")

    def test_a_card_that_gives_a_source_is_joined(self):
        row = board.item_row(self.rows()[0], project="p",
                             card={"source_refs": ["topic:abc"],
                                   "aliases": ["REQ4"]},
                             card_known=True, binding=None)
        self.assertEqual(row["source"]["state"], "JOINED")
        self.assertEqual(row["source"]["source_refs"], ["topic:abc"])
        self.assertEqual(row["source"]["aliases"], ["REQ4"])

    def rows_with(self, store_records: dict) -> list[dict]:
        """Composed from the STORE's shape, through the real translation.

        The store is keyed by `session_ref` with `bound_item_ref` inside the
        record; the board indexes bindings by Item. `_bindings_by_item` is
        what crosses that, so the tests feed it the store's shape rather than
        the shape `compose` happens to accept — a test that hand-writes the
        consumer's input is immune to the producer changing, which is how a
        join stops being tested while still looking tested.
        """
        return board.compose([a_board("p", [an_item("X")])],
                             bindings=board._bindings_by_item(store_records))["items"]

    def test_only_an_open_binding_names_a_session(self):
        """A closed thread is history, and this column is 'who is working now'.

        Without the OPEN filter a board would keep showing a reader an Item as
        staffed by a session that ended, which is worse than showing nothing.
        """
        self.assertIsNone(self.rows_with(
            {"s1": {"state": "CLOSED", "session_role": "execution",
                    "bound_item_ref": "p/X", "session_ref": "s1"}})[0]
            ["execution_session"])
        self.assertEqual(self.rows_with(
            {"s1": {"state": "OPEN", "session_role": "execution",
                    "bound_item_ref": "p/X", "session_ref": "s1"}})[0]
            ["execution_session"], "s1")

    def test_a_binding_for_another_item_is_not_this_items_session(self):
        rows = self.rows_with(
            {"s1": {"state": "OPEN", "session_role": "execution",
                    "bound_item_ref": "p/OTHER", "session_ref": "s1"}})
        self.assertIsNone(rows[0]["execution_session"])

    def test_the_execution_session_wins_over_the_coordinator(self):
        """Two roles may be open on one Item; the work is done by one of them."""
        rows = self.rows_with(
            {"c": {"state": "OPEN", "session_role": "coordinator",
                   "bound_item_ref": "p/X", "session_ref": "c"},
             "e": {"state": "OPEN", "session_role": "execution",
                   "bound_item_ref": "p/X", "session_ref": "e"}})
        self.assertEqual(rows[0]["execution_session"], "e")
        self.assertEqual(rows[0]["session_role"], "execution")


# ---------------------------------------------------------------------------
# What the projection refuses to compose.
# ---------------------------------------------------------------------------


class Refusals(unittest.TestCase):
    def test_a_document_that_is_not_a_board_is_refused(self):
        document = a_board("p", [an_item("X")])
        document["schema"] = "serveros/board.v2"
        with self.assertRaises(board.Refused) as caught:
            board.compose([document])
        self.assertIn("serveros/board.v3", str(caught.exception))

    def test_one_item_in_two_boards_is_refused(self):
        """One Item has one project; a duplicate reads as two Items."""
        with self.assertRaises(board.Refused) as caught:
            board.compose([a_board("p", [an_item("X")]),
                           a_board("p", [an_item("X")])])
        self.assertIn("one project", str(caught.exception))

    def test_the_same_id_in_two_projects_is_two_items(self):
        """The guard above must not be widened into forbidding that.

        `LIT-0101` and `SRV-0001` are different Items with different ids, and
        the real board carries one namespace per project; an id-only key would
        have refused two genuinely distinct Items that happen to share nothing.
        """
        document = board.compose([a_board("p", [an_item("X")]),
                                  a_board("q", [an_item("X")])])
        self.assertEqual(document["counts"]["items"], 2)

    def test_a_relative_output_directory_is_refused(self):
        """Otherwise the board lands in whatever directory the caller was in."""
        with self.assertRaises(board.Refused) as caught:
            board.render({"counts": {"items": 0}, "generated_at": "x",
                          "source": {"projects": []}, "priority": {},
                          "sections": []}, "somewhere/relative")
        self.assertIn("absolute", str(caught.exception))


# ---------------------------------------------------------------------------
# The document a reader gets.
# ---------------------------------------------------------------------------


class TheDocument(unittest.TestCase):
    def compose(self, items, **kwargs):
        return board.compose([a_board("p", items)], **kwargs)

    def test_the_five_levels_are_present_and_empty(self):
        """§S's default display, rendered empty rather than filled by proxy.

        Bucketing by `kind` or by `state` and printing the result under P0–P4
        would invent a taxonomy and present it in a column a reader has been
        told means priority. The empty buckets are the honest rendering and
        this test is what stops them being quietly populated later.
        """
        document = self.compose([an_item("X")])
        levels = [s for s in document["sections"] if s["kind"] == "priority"]
        self.assertEqual([s["key"] for s in levels],
                         list(board.DECLARED_LEVELS))
        for section in levels:
            self.assertEqual(section["items"], [])

    def test_the_priority_axis_says_who_produces_it(self):
        priority = self.compose([an_item("X")])["priority"]
        self.assertEqual(priority["state"], "UNPRODUCED")
        self.assertIsNone(priority["producer"])
        self.assertTrue(priority["because"])

    def test_a_letter_in_a_title_is_reported_and_never_used(self):
        """The one real signal about priority, kept out of the buckets.

        Reporting that a string occurs is not reading it as a fact. The field
        exists so the decision-maker has the evidence; it must not be able to
        fill a section, which is what the second assertion checks by putting
        an Item whose title literally starts with `P0` through and finding the
        P0 section still empty.
        """
        document = self.compose([an_item("X", title="P0 fix the thing")])
        named = document["priority"]["titles_naming_a_level"]
        self.assertEqual([row["item_ref"] for row in named], ["p/X"])
        self.assertEqual(named[0]["literal"], "P0")
        p0 = next(s for s in document["sections"] if s["key"] == "P0")
        self.assertEqual(p0["items"], [])

    def test_a_level_in_a_completed_title_is_still_reported(self):
        """The correction: the scan is over the corpus, not over the actives.

        The first version scanned active rows only, found one of the three
        real occurrences, and read as a complete list. A count is evidence
        only if the set it counts over is the set the question is about.
        """
        document = self.compose([an_item("X", title="P1 done and closed",
                                         state="CLOSED")])
        self.assertEqual([r["item_ref"] for r in
                          document["priority"]["titles_naming_a_level"]], ["p/X"])

    def test_completed_items_move_and_are_struck_through(self):
        document = self.compose([an_item("A", state="CLOSED"),
                                 an_item("B", state="CANCELLED"),
                                 an_item("C", state="ACTIVE")])
        completed = next(s for s in document["sections"]
                         if s["key"] == board.COMPLETED)
        self.assertEqual([r["id"] for r in completed["items"]], ["A", "B"])
        markdown = board.markdown(document)
        self.assertIn("~~item A~~", markdown)
        self.assertIn("~~item B~~", markdown)
        self.assertNotIn("~~item C~~", markdown)

    def test_a_reopened_item_loses_the_strikethrough_with_no_board_write(self):
        """Reopen is a state change in the authority and nothing else.

        §S asks for "先 Workengine state change → regenerate board → 去删除线".
        There is no board-side step because there is no board-side fact: the
        tildes are derived, so an Item that is no longer CLOSED cannot render
        as one, and no reconciliation is possible because nothing was stored.
        """
        closed = self.compose([an_item("A", state="CLOSED")])
        self.assertIn("~~item A~~", board.markdown(closed))
        reopened = self.compose([an_item("A", state="AVAILABLE")])
        self.assertNotIn("~~item A~~", board.markdown(reopened))
        self.assertEqual(reopened["counts"]["completed"], 0)

    def test_the_title_survives_completion(self):
        """§S: keep the title, keep the history. A projection stores nothing."""
        document = self.compose([an_item("A", state="CLOSED")])
        self.assertEqual(document["items"][0]["title"], "item A")


# ---------------------------------------------------------------------------
# Writing. The board is regenerated, never edited.
# ---------------------------------------------------------------------------


class Writing(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.boards = self.root / "boards"
        (self.boards / "p").mkdir(parents=True)
        (self.boards / "p" / "current.json").write_text(
            json.dumps(a_board("p", [an_item("X")])), encoding="utf-8")
        self.out = self.root / "requirement-board"

    def build(self, **kwargs):
        """`build()` with locators resolved against this fixture's root.

        The resolver is not defaulted in the module — see its docstring — so
        every call here has to say what a relative locator is relative to,
        exactly as the CLI says the workspace root.
        """
        return board.build(boards_root=self.boards,
                           card_locator_resolver=lambda loc: str(self.root / loc),
                           **kwargs)

    def test_both_files_are_written_and_agree(self):
        document = self.build()
        written = board.render(document, self.out)
        self.assertEqual(Path(written["json"]).name, "current.json")
        self.assertEqual(Path(written["markdown"]).name, "current.md")
        on_disk = json.loads(Path(written["json"]).read_text(encoding="utf-8"))
        self.assertEqual(on_disk["schema"], board.SCHEMA)
        self.assertIn("需求总看板", Path(written["markdown"]).read_text(
            encoding="utf-8"))

    def test_a_second_render_replaces_rather_than_appends(self):
        document = self.build()
        board.render(document, self.out)
        first = (self.out / "current.md").read_text(encoding="utf-8")
        board.render(document, self.out)
        self.assertEqual((self.out / "current.md").read_text(encoding="utf-8"),
                         first)
        self.assertEqual([p.name for p in sorted(self.out.iterdir())],
                         ["current.json", "current.md"])

    def test_the_board_is_never_read_back_as_a_project_board(self):
        """The self-read loop the first draft would have walked into.

        `load_boards` globs `*/current.json` under the boards root. Writing the
        global board there made the next run read it as a board for a project
        called `global` and refuse the composition over a schema mismatch it
        had written itself — a failure that appears on the SECOND run, which
        is the worst place for one to appear.
        """
        first = self.build()
        board.render(first, self.out)
        second = self.build()
        self.assertEqual(second["counts"], first["counts"])
        self.assertEqual(second["source"]["projects"], ["p"])

    def test_an_unreadable_card_is_reported_rather_than_skipped(self):
        card = self.root / "card.yaml"
        card.write_text("title: [unclosed\n", encoding="utf-8")
        self.boards.joinpath("p", "current.json").write_text(
            json.dumps(a_board("p", [an_item("X", card_locator=str(card))])),
            encoding="utf-8")
        document = self.build()
        self.assertEqual(document["items"][0]["source"]["state"], "UNREADABLE")
        self.assertEqual(document["counts"]["without_a_card"], 1)

    def test_a_board_root_that_is_not_there_is_no_projects_not_a_failure(self):
        document = board.build(boards_root=self.root / "nowhere",
                               card_locator_resolver=lambda loc: str(self.root / loc))
        self.assertEqual(document["counts"]["items"], 0)
        self.assertEqual(document["source"]["projects"], [])


# ---------------------------------------------------------------------------
# The command. Nothing above this line touches the real root contract.
# ---------------------------------------------------------------------------


class TheCommand(unittest.TestCase):
    """`serverosctl requirement board`, against a contract in tmp_path.

    The module is tested above; this is the wiring, and the wiring is where
    the two things that cannot be checked in a pure function live: which root
    a path resolves to, and what the command refuses to write.
    """

    def setUp(self):
        import os
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name) / "data"
        self.workspace = self.data / "workspace"
        for path in (self.data / "state" / "serveros", self.workspace / "records"):
            path.mkdir(parents=True, exist_ok=True)
        for key, value in (("HOME", str(Path(self._tmp.name) / "home")),
                           ("DATA_ROOT", str(self.data)),
                           ("WORKSPACE_ROOT", str(self.workspace)),
                           ("ARTIFACTS_ROOT", str(self.data / "artifacts")),
                           ("TOOLCHAINS_ROOT", str(self.data / "toolchains"))):
            self.enterContext(mock.patch.dict(os.environ, {key: value}))
        # The repo module caches by name in sys.modules; the CLI loads the
        # board module itself, so the environment above is what it sees.
        self.cli = _load("serveros_runtime_cli", REPO / "runtime" / "cli.py")
        self.boards = self.data / "state" / "serveros" / "boards" / "p"
        self.boards.mkdir(parents=True, exist_ok=True)
        card = self.workspace / "records" / "X" / "card.yaml"
        card.parent.mkdir(parents=True, exist_ok=True)
        card.write_text("aliases: [REQ-X]\n", encoding="utf-8")
        (self.boards / "current.json").write_text(json.dumps(
            a_board("p", [an_item("X", card_locator="records/X/card.yaml")])),
            encoding="utf-8")

    def run_cli(self, *argv):
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_the_relative_locator_resolves_against_the_workspace_root(self):
        """Not against the cwd. The card lives under `records/` there.

        The command is run from whatever directory pytest was started in, and
        the assertion is that the card joined anyway — a locator resolved
        against the cwd would report NOT_FOUND and the board would look
        complete with the whole source column empty.
        """
        code, out, _ = self.run_cli("requirement", "board", "--json")
        self.assertEqual(code, 0)
        document = json.loads(out)
        self.assertEqual(document["items"][0]["source"]["state"], "JOINED")
        self.assertEqual(document["items"][0]["source"]["aliases"], ["REQ-X"])

    def test_it_prints_and_writes_nothing_by_default(self):
        """The board is a projection. Inspecting it must not leave a file."""
        code, _, _ = self.run_cli("requirement", "board")
        self.assertEqual(code, 0)
        self.assertFalse((self.data / "state" / "serveros"
                          / "requirement-board").exists())

    def test_write_lands_in_the_contracts_namespace(self):
        code, _, _ = self.run_cli("requirement", "board", "--write")
        self.assertEqual(code, 0)
        written = self.data / "state" / "serveros" / "requirement-board"
        self.assertEqual(sorted(p.name for p in written.iterdir()),
                         ["current.json", "current.md"])

    def test_write_into_the_boards_root_is_refused(self):
        """The self-read loop, refused at the only place that can see both.

        `render()` does not know where the boards root is, so this guard lives
        in the command — and it is the guard that matters, because the failure
        it prevents does not appear until the run AFTER the bad one.
        """
        code, _, err = self.run_cli("requirement", "board", "--write",
                                    "--output-dir",
                                    str(self.boards.parent / "global"))
        self.assertEqual(code, 1)
        self.assertIn("boards root", err)
        self.assertFalse((self.boards.parent / "global").exists())

    def test_write_to_a_relative_directory_is_refused(self):
        code, _, err = self.run_cli("requirement", "board", "--write",
                                    "--output-dir", "out")
        self.assertEqual(code, 1)
        self.assertIn("absolute", err)
        self.assertFalse(Path("out").exists())


if __name__ == "__main__":
    unittest.main()
