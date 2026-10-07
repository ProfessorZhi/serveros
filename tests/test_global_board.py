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
import io
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
# A page carries two dates, and the second one is when the state was taken.
# ---------------------------------------------------------------------------


class TheStateHasAnAgeAndThePageSaysIt(unittest.TestCase):
    """`board-contract.yaml` carries `generated_at` on each board document for
    one stated reason: "a reader has to be able to tell a fresh board from one
    generated before the change they are looking for." A composition that
    prints only its own `generated_at` undoes that — it dates Item state the
    control plane measured days ago to this second, on the page a person reads.
    """

    def test_the_boards_own_time_is_carried_and_not_replaced(self):
        old = a_board("p", [an_item("A")])
        old["generated_at"] = "2026-01-01T00:00:00+00:00"
        document = board.compose([old])
        self.assertEqual(document["source"]["board_generated_at"],
                         {"p": "2026-01-01T00:00:00+00:00"})
        # The composition's own time is a different fact, and it is still the
        # composition's: both are present, neither stands in for the other.
        self.assertNotEqual(document["generated_at"],
                            document["source"]["board_generated_at"]["p"])

    def test_every_project_is_dated_separately(self):
        """One project's board can be fresh while another's is not, and a
        single oldest-or-newest scalar would hide exactly that."""
        fresh = a_board("fresh", [an_item("A")])
        fresh["generated_at"] = "2026-09-27T00:00:00+00:00"
        stale = a_board("stale", [an_item("B")])
        stale["generated_at"] = "2026-09-10T00:00:00+00:00"
        document = board.compose([fresh, stale])
        self.assertEqual(document["source"]["board_generated_at"],
                         {"fresh": "2026-09-27T00:00:00+00:00",
                          "stale": "2026-09-10T00:00:00+00:00"})

    def test_the_human_page_prints_when_the_state_was_taken(self):
        old = a_board("p", [an_item("A")])
        old["generated_at"] = "2026-01-01T00:00:00+00:00"
        text = board.markdown(board.compose([old]))
        self.assertIn("2026-01-01T00:00:00+00:00", text)
        # And the age, so a person does not have to subtract dates: composed
        # 2026-09-27, taken 2026-01-01.
        self.assertIn("天前", text)

    def test_a_board_with_no_time_still_composes_and_says_nothing_extra(self):
        """An absent `generated_at` is a fact about that document. Inventing a
        date for it — or refusing the whole composition over it — would both be
        worse than saying only what this page can."""
        undated = a_board("p", [an_item("A")])
        del undated["generated_at"]
        document = board.compose([undated])
        self.assertEqual(document["source"]["board_generated_at"], {})
        text = board.markdown(document)
        self.assertNotIn("天前", text)
        self.assertIn("item A", text)

    def test_an_unreadable_time_is_not_turned_into_an_age(self):
        """A timestamp this module cannot parse is one it will not subtract.

        Guessing an age from a malformed value would print a measurement
        nobody made, which is the failure the whole second date exists to
        prevent.
        """
        self.assertIsNone(board._age_days("2026-09-27T00:00:00+00:00", "not a time"))
        self.assertIsNone(board._age_days(None, "2026-01-01T00:00:00+00:00"))
        # A naive timestamp is not a duration until someone says which zone it
        # was written in.
        self.assertIsNone(board._age_days("2026-09-27T00:00:00+00:00",
                                          "2026-01-01T00:00:00"))
        self.assertEqual(board._age_days("2026-09-27T00:00:00+00:00",
                                         "2026-09-10T00:00:00+00:00"), 17)

    def test_a_malformed_time_is_printed_but_not_dated(self):
        """The text is copied verbatim — "it says this" is still true — while
        the age, which would be this module's own claim, is left off."""
        odd = a_board("p", [an_item("A")])
        odd["generated_at"] = "whenever"
        text = board.markdown(board.compose([odd]))
        self.assertIn("whenever", text)
        self.assertNotIn("天前", text)


# ---------------------------------------------------------------------------
# The page a phone reads. §5 asks for blocks, not columns.
# ---------------------------------------------------------------------------


class TheHumanPageIsBuiltForItsReader(unittest.TestCase):
    """§5 shapes the page: 未完成 as blocks, 已完成 folded, 待排 rather than the
    machine token, and a region that belongs to a person.

    The table this replaced had eight columns. At 390 px a reader scrolls
    sideways on every row and loses which Item the row is about, which is the
    one thing a board is for.
    """

    def compose(self, items, **kwargs):
        return board.compose([a_board("p", items)], **kwargs)

    def test_an_item_is_lines_rather_than_a_row(self):
        text = board.markdown(self.compose([an_item("X")]))
        self.assertIn("#### p/X — item X", text)
        self.assertNotIn("| Item | Title |", text)
        self.assertIn("- 状态：ACTIVE", text)

    def test_the_absent_priority_is_the_readers_word_not_the_stores(self):
        """One fact, two audiences: the document carries `UNPRODUCED`, the page
        says 待排. Neither is a filled-in bucket, and the page must not print a
        machine state where §5 asked for a phrase."""
        document = self.compose([an_item("X")])
        self.assertEqual(document["items"][0]["priority"], None)
        text = board.markdown(document)
        self.assertIn("- 优先级：待排", text)

    def test_a_completed_item_keeps_its_title_priority_and_resolution(self):
        text = board.markdown(self.compose([an_item("A", state="CLOSED",
                                                    outcome="FULFILLED")]))
        self.assertIn("~~item A~~", text)
        self.assertIn("- 优先级：待排", text)
        self.assertIn("- 结论：CLOSED · FULFILLED", text)

    def test_a_completed_item_with_no_outcome_says_so(self):
        """`未记录` is a fact about the fabric. Printing nothing there would
        read as a resolution of the empty string."""
        text = board.markdown(self.compose([an_item("A", state="CLOSED")]))
        self.assertIn("- 结论：CLOSED（outcome 未记录）", text)

    def test_completed_is_folded_by_default(self):
        text = board.markdown(self.compose([an_item("A", state="CLOSED"),
                                            an_item("C")]))
        self.assertIn("<details>", text)
        self.assertIn("<summary>展开 1 项", text)
        # Folded and still complete: the strikethrough is inside the fold, not
        # replaced by the fold.
        self.assertIn("~~item A~~", text)
        self.assertLess(text.index("<details>"), text.index("~~item A~~"))

    def test_the_priority_state_is_on_the_page_and_the_argument_is_behind_it(self):
        """§5's "机器字段折叠到证据入口", measured rather than assumed.

        The paragraph explaining why no Item has a P-level is one sentence of
        state and eight lines of argument. Printed flat at 390 px it put the
        first Item below the fold on every visit; the state stays visible and
        the argument is one tap away. The document is unchanged — this is a
        rendering of the fact, not a shortening of it.
        """
        document = self.compose([an_item("X")])
        text = board.markdown(document)
        self.assertIn("> 优先级：**UNPRODUCED**（待排）", text)
        self.assertEqual(document["priority"]["because"], board.PRIORITY_BECAUSE)
        self.assertIn(board.PRIORITY_BECAUSE, text)
        self.assertLess(text.index("<details>"), text.index(board.PRIORITY_BECAUSE))

    def test_a_missing_card_is_said_once_and_not_per_row(self):
        """Twelve empty 来源 columns are two different facts and only one of
        them is about the Item. The note keeps them apart."""
        document = self.compose([an_item("A"), an_item("B")])
        self.assertEqual(document["counts"]["without_a_card"], 2)
        text = board.markdown(document)
        self.assertIn("2 行没有可读的 card.yaml", text)

    def test_a_page_with_every_card_joined_does_not_claim_a_gap(self):
        document = board.compose([a_board("p", [an_item("A")])],
                                 cards={"p/A": {"aliases": ["REQ-1"]}})
        self.assertEqual(document["counts"]["without_a_card"], 0)
        text = board.markdown(document)
        self.assertNotIn("没有可读的 card.yaml", text)
        self.assertIn("- 来源：REQ-1", text)


# ---------------------------------------------------------------------------
# An empty 来源 column has two causes, and they are fixed in two places.
# ---------------------------------------------------------------------------


class TheTwoCausesOfAnEmptySourceColumn(unittest.TestCase):
    """`how` travels from the resolver to the page, and it is the only thing
    that keeps two different facts from being printed as one.

    The personal board re-hangs a `card_locator` written against another
    machine's workspace root and reads the card from THIS node; the
    requirement board had its own two-line rule that took such a locator as
    absolute-as-written. The same card was therefore JOINED on one page and
    absent on the other, and the published requirement board said
    「那张卡不在本机」 about the litellm cards sitting in
    `records/projects/litellm/items/`. These tests hold the page to one rule.

    The distinction the counts exist for: REBASED means the card WAS read and
    the locator is still wrong, so the fix is a pointer. UNRESOLVED means
    nothing under this node's roots matched, so the fix is a card. Sending a
    reader to the wrong one of those is what the note at the top of §5's page
    is for, and it is why `how` is carried at all.
    """

    def composed(self, cards=None, resolutions=None):
        return board.compose([a_board("p", [an_item("X")])], cards=cards,
                             card_resolutions=resolutions)

    def test_a_rebased_card_is_read_and_the_locator_is_still_reported(self):
        """The card is on this node; the pointer is to the other machine.

        Counted in `cards_rebased` and NOT in `without_a_card`: the row is
        correct and the 来源 column beside it is filled in, so a page that
        said "N 行没有可读的 card.yaml" would be contradicting its own table.
        """
        document = self.composed(
            cards={"p/X": {"source_refs": ["topic:abc"]}},
            resolutions={"p/X": {"path": "/here/p/X/card.yaml",
                                 "how": "REBASED",
                                 "locator": "/opt/other/node/card.yaml"}})
        self.assertEqual(document["items"][0]["source"]["state"], "JOINED")
        self.assertEqual(document["counts"]["cards_rebased"], 1)
        self.assertEqual(document["counts"]["without_a_card"], 0)
        text = board.markdown(document)
        self.assertIn("1 行的 card_locator 写的是另一台机器的路径", text)
        self.assertNotIn("没有可读的 card.yaml", text)
        self.assertIn("- 来源：topic:abc", text)

    def test_a_rebased_locator_that_found_nothing_is_not_a_rebased_card(self):
        """Re-hanging is a rule, not a result. The count is about the result.

        `cards_rebased` requires `state == JOINED` as well as `how == REBASED`,
        which makes the two counts disjoint. Without that, a request to re-hang
        a locator that then matched nothing would be reported as a card read
        off the other machine's path — a claim about a file nobody opened.
        """
        document = self.composed(
            cards={},
            resolutions={"p/X": {"path": "/here/p/X/card.yaml",
                                 "how": "REBASED",
                                 "locator": "/opt/other/node/card.yaml"}})
        self.assertEqual(document["items"][0]["source"]["state"], "NOT_FOUND")
        self.assertEqual(document["counts"]["cards_rebased"], 0)
        self.assertEqual(document["counts"]["without_a_card"], 1)
        self.assertNotIn("写的是另一台机器的路径",
                         board.markdown(document))

    def test_an_unresolved_locator_names_the_pointer_as_the_thing_to_fix(self):
        document = self.composed(
            cards={},
            resolutions={"p/X": {"path": "", "how": "UNRESOLVED",
                                 "locator": "/opt/other/node/card.yaml"}})
        self.assertEqual(document["counts"]["without_a_card"], 1)
        self.assertEqual(document["counts"]["cards_unresolved"], 1)
        text = board.markdown(document)
        self.assertIn("1 行没有可读的 card.yaml", text)
        self.assertIn("其中 1 行的 card_locator 在该根下解析不到文件", text)

    def test_the_note_names_the_root_it_read_and_not_the_machine(self):
        """The false sentence this page actually carried, kept out.

        The published board said 「那张卡不在本机」 about the marketops cards
        while they sat on this node in `worktrees/records/MKT-0001` — a
        checkout of the records branch `marketops/intake` that the board's
        canonical `records/` root does not have. The board is right not to
        read a worktree: that branch has not landed, and a page composed from
        it would publish un-landed work and would depend on a directory
        another actor may remove. So the sentence has to be about the root.
        """
        document = self.composed()
        text = board.markdown(document)
        self.assertIn("本看板读的 `records/` 根下没有这张卡", text)
        for claim in ("不在本机", "本机没有", "本机解析不到"):
            self.assertNotIn(claim, text)

    def test_a_row_with_no_locator_does_not_blame_a_locator(self):
        """The other sub-cause. Nothing is broken; there is nothing to point
        at, and a page that sent this reader hunting for a bad pointer would
        be inventing the pointer."""
        document = self.composed()
        self.assertEqual(document["counts"]["without_a_card"], 1)
        self.assertEqual(document["counts"]["cards_unresolved"], 0)
        text = board.markdown(document)
        self.assertIn("1 行没有可读的 card.yaml", text)
        self.assertNotIn("解析不到文件", text)

    def test_both_causes_are_on_the_page_at_once(self):
        document = board.compose(
            [a_board("p", [an_item("X"), an_item("Y")])],
            cards={"p/X": {"source_refs": ["topic:abc"]}},
            card_resolutions={"p/X": {"path": "/here/p/X/card.yaml",
                                      "how": "REBASED",
                                      "locator": "/opt/other/card.yaml"},
                              "p/Y": {"path": "", "how": "UNRESOLVED",
                                      "locator": "/opt/other/card.yaml"}})
        self.assertEqual(document["counts"]["cards_rebased"], 1)
        self.assertEqual(document["counts"]["without_a_card"], 1)
        self.assertEqual(document["counts"]["cards_unresolved"], 1)
        text = board.markdown(document)
        self.assertIn("1 行的 card_locator 写的是另一台机器的路径", text)
        self.assertIn("2 行里 1 行没有可读的 card.yaml", text)

    def test_build_hands_the_resolver_the_project_and_the_item(self):
        """The contract that broke. A locator alone is not enough to resolve.

        `resolve_card_path` falls back to the conventional
        `records/<project>/items/<id>/card.yaml` when the locator does not
        land, so the project and the item id are inputs to the rule and not
        decoration. The first version of this call took only the locator, and
        a resolver that needed the other two could not be written at all.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "boards" / "p").mkdir(parents=True)
            (root / "boards" / "p" / "current.json").write_text(
                json.dumps(a_board("p", [an_item("X")])), encoding="utf-8")
            seen = []

            def resolver(project, item_id, locator):
                seen.append((project, item_id, locator))
                return {"path": "", "how": "DECLARED", "why": "no file",
                        "locator": locator}

            document = board.build(boards_root=root / "boards",
                                   card_locator_resolver=resolver)
        self.assertEqual(seen, [("p", "X", "records/X/card.yaml")])
        self.assertEqual(document["items"][0]["source"]["resolution"]["how"],
                         "DECLARED")


# ---------------------------------------------------------------------------
# The one region of the page that is not derived.
# ---------------------------------------------------------------------------


class TheHumanRegionBelongsToTheReader(unittest.TestCase):
    """The board is regenerated. A person's note on it is not regenerated.

    These two rules pull against each other in the same file, and the failure
    they produce when unbalanced is silent: somebody's sentence is there until
    the next run, and then it is not.
    """

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

    def document(self):
        return board.build(boards_root=self.boards,
                           card_locator_resolver=lambda project, item_id, loc: {
                               "path": str(self.root / loc) if loc else "",
                               "how": "DECLARED", "locator": loc,
                               "why": ""})

    def page(self):
        return (self.out / "current.md").read_text(encoding="utf-8")

    def test_a_fresh_page_says_what_the_region_is(self):
        board.render(self.document(), self.out)
        self.assertIn(board.HUMAN_BEGIN, self.page())
        self.assertIn(board.HUMAN_EMPTY, self.page())

    def test_a_note_survives_the_next_regeneration(self):
        board.render(self.document(), self.out)
        note = "老张：LIT-0215 的 brief 里那条保留段已经和 0216 撞了。"
        self.out.joinpath("current.md").write_text(
            self.page().replace(board.HUMAN_EMPTY, note), encoding="utf-8")
        board.render(self.document(), self.out)
        self.assertIn(note, self.page())
        # And the rest of the page was rebuilt rather than frozen: the note did
        # not take the projection's place.
        self.assertIn("#### p/X — item X", self.page())

    def test_a_second_render_with_a_note_is_stable(self):
        # ONE document for both renders. Composing twice would stamp two
        # different `generated_at` values and the comparison would be of two
        # boards rather than of one board rendered twice — which is the thing
        # the note is supposed to survive.
        document = self.document()
        board.render(document, self.out)
        self.out.joinpath("current.md").write_text(
            self.page().replace(board.HUMAN_EMPTY, "一条批注"), encoding="utf-8")
        board.render(document, self.out)
        once = self.page()
        board.render(document, self.out)
        self.assertEqual(self.page(), once)

    def test_a_note_is_not_read_from_inside_the_board(self):
        """Text above the opening marker is the board's, not the reader's.

        A run that carried the whole previous page forward would turn the board
        into an append-only log — and every row it added would be a row that
        stopped being derived.
        """
        board.render(self.document(), self.out)
        page = self.page()
        self.out.joinpath("current.md").write_text(
            page.replace("# 需求总看板", "stale line nobody updated"), encoding="utf-8")
        board.render(self.document(), self.out)
        self.assertIn("# 需求总看板", self.page())
        self.assertNotIn("stale line nobody updated", self.page())

    def test_an_unclosed_region_is_dropped_loudly_and_does_not_grow(self):
        """The unbalanced case, and why "" is the answer rather than the tail.

        Carrying the unclosed text forward would re-emit it with a fresh
        closing marker, so one missing marker would grow the block once per
        render. Losing the note is smaller — and it is not silent: the reason
        goes to stderr, which is what a person needs to fix the marker.
        """
        board.render(self.document(), self.out)
        self.out.joinpath("current.md").write_text(
            self.page().replace(board.HUMAN_END, ""), encoding="utf-8")
        buffer = io.StringIO()
        with mock.patch.object(sys, "stderr", buffer):
            board.render(self.document(), self.out)
        self.assertIn(board.HUMAN_END, self.page())
        self.assertIn("人工批注读不出来", buffer.getvalue())
        self.assertEqual(self.page().count(board.HUMAN_BEGIN), 1)

    def test_a_second_opening_marker_is_a_reason_and_not_a_guess(self):
        board.render(self.document(), self.out)
        page = self.page()
        doubled = page.replace(board.HUMAN_BEGIN,
                               board.HUMAN_BEGIN + "another\n" + board.HUMAN_BEGIN)
        self.out.joinpath("current.md").write_text(doubled, encoding="utf-8")
        text, why = board.extract_human(self.out.joinpath("current.md").read_text())
        self.assertEqual(text, "")
        self.assertTrue(why)


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
                           card_locator_resolver=lambda project, item_id, loc: {
                               "path": str(self.root / loc) if loc else "",
                               "how": "DECLARED", "locator": loc,
                               "why": ""},
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
                               card_locator_resolver=lambda project, item_id, loc: {
                               "path": str(self.root / loc) if loc else "",
                               "how": "DECLARED", "locator": loc,
                               "why": ""})
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
