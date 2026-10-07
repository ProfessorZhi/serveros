#!/usr/bin/env python3
"""§6/§8 — the personal board, and the three ways a projection lies.

WHY THIS FILE EXISTS
--------------------
Every defect this module has had in its life was the same defect with a
different field name: **a fact about this program reported as a fact about the
world.** Four of them, all found by running the thing against this node rather
than by reading it:

  * a card locator was ignored and the path recomposed from parts, so 17 real
    cards read `MISSING` while they sat on the disk;
  * `design.md` was named where the records hold `design/`, so every design,
    plan and report read as unwritten;
  * `<records>/<project>/` was composed in four places where the real layout is
    `<records>/projects/<project>/`, so `today.yaml` was written where nothing
    reads it and the board answered "nobody has chosen" about a project where
    somebody had;
  * a section list copied from §6 named nine of work-engine's ten states, and
    four in-flight Items were reported as claimed by nobody.

None of those is a crash. Each produced a confident, plausible, wrong sentence
a person would have acted on. So the tests below are mostly about the
DISTINCTION between two blanks — `UNPRODUCED` against `EMPTY`, `MISSING`
against `NOT_YET_REQUIRED`, a broken pointer against an absent card.

THE ONE TEST THAT IS NOT ABOUT THIS MODULE
------------------------------------------
`TheBoardPlacesEveryState::test_every_workengine_state_is_named_by_a_section`
parses `workengine/model.py` for `ItemState`'s members and fails if any of them
names no section. The list in `intake/workchain-contract.yaml` is therefore
pinned to the AUTHORITY and not to a comment: the day work-engine gains an
eleventh state this file goes red, instead of the new state's Items going
unclaimed on a board nobody can tell is incomplete.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
import tempfile
import unittest
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


chain = _load("serveros_intake_workchain", REPO / "intake" / "workchain.py")
paths_mod = _load("serveros_runtime_paths", REPO / "runtime" / "paths.py")

WORK_ENGINE = (paths_mod.Paths().root("workspace") / "src" / "work-engine"
               / "workengine")

#: The fixture, and the three roots it is read with. Absolute, because the
#: command refuses a relative root — a board composed from whatever directory
#: the caller was standing in is a board nobody else can reproduce, and a test
#: that relied on the cwd would pass or fail with where it was run.
SAMPLE = REPO / "intake" / "samples" / "workchain"
CONTRACT = chain.load_contract(REPO / "intake" / "workchain-contract.yaml")


def a_projection(project: str, items: list[dict]) -> dict:
    return {"schema": "serveros/board.v3", "project": project,
            "generated_at": "2026-01-01T00:00:00+00:00",
            "generation_source_revision": None, "items": items}


def an_item(item_id: str, **overrides) -> dict:
    item = {"id": item_id, "title": f"item {item_id}", "kind": "feature",
            "state": "ACTIVE", "outcome": None, "card_locator": None,
            "activities": [], "deliverables": []}
    item.update(overrides)
    return item


# ---------------------------------------------------------------------------
# The state list comes from the authority, not from the contract's comment.
# ---------------------------------------------------------------------------

class TheBoardPlacesEveryState(unittest.TestCase):
    def _authority_states(self) -> set[str]:
        model = WORK_ENGINE / "model.py"
        if not model.is_file():
            self.skipTest(
                f"{model} is not on this node, so the board's section list "
                f"cannot be checked against the authority here. The list is "
                f"reported as UNVERIFIED, not as confirmed.")
        tree = ast.parse(model.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "ItemState":
                return {n.targets[0].id for n in node.body
                        if isinstance(n, ast.Assign)
                        and isinstance(n.targets[0], ast.Name)}
        self.fail("ItemState was not found in the authority's model.py")

    def test_every_workengine_state_is_named_by_a_section(self):
        """A state §6's sections do not name is a state whose Items vanish.

        The live board proved this the expensive way: `ACTIVE` was named
        nowhere, and four Items actually being worked on rendered as
        `未被任何栏目认领`. The failure mode is not a crash, it is a board that
        looks complete.
        """
        states = self._authority_states()
        named = set()
        for spec in CONTRACT["personal_board"]["sections"]:
            named |= set((spec.get("selects") or {}).get("state") or ())
        missing = sorted(states - named)
        self.assertEqual(
            missing, [],
            f"work-engine has {sorted(states)} and `personal_board.sections` "
            f"names no section for {missing}. Every Item in those states would "
            f"render as unclaimed, and a reader cannot tell that from 'no work "
            f"is in those states'.")
        # ...and the other direction: a section selecting a state the authority
        # does not have is a section that can never hold anything.
        invented = sorted(s for s in named if s not in states)
        self.assertEqual(
            invented, [],
            f"`personal_board.sections` selects {invented}, which work-engine "
            f"does not have. A section for a state nobody can be in is an "
            f"empty section that reads as a quiet one.")

    def test_no_item_is_placed_twice_and_the_sections_account_for_all(self):
        """Every Item exactly once, so the section counts can be added up.

        `today: false` on every section but 今日重点 is what makes this true:
        the authored selection is a cut across ALL states, so without the
        filter a chosen Item would appear both as the person's focus and in its
        own state bucket, and the section counts would sum to more than the
        number of Items on the board.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "boards").mkdir()
            (root / "records" / "projects").mkdir(parents=True)
            (root / "workspace").mkdir()
            states = self._authority_states()
            for index, state in enumerate(sorted(states)):
                project = f"p{index}"
                out = root / "boards" / project / "current.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(
                    a_projection(project, [an_item(f"IT-{index:03d}",
                                                   state=state)])),
                    encoding="utf-8")
                (root / "records" / "projects" / project / "items"
                 / f"IT-{index:03d}").mkdir(parents=True)
                (root / "records" / "projects" / project / "items"
                 / f"IT-{index:03d}" / "card.yaml").write_text(
                    f"id: IT-{index:03d}\n", encoding="utf-8")
            projects = chain.load_projects(boards_root=root / "boards")
            board = chain.compose_board(
                contract=CONTRACT, projects=projects,
                records_root=root / "records", workspace_root=root / "workspace")
        placed = [row["id"] for section in board["sections"]
                  for row in section["rows"]]
        self.assertEqual(sorted(placed), sorted(set(placed)),
                         "an Item is in two sections, so the board's counts "
                         "cannot be added up")
        self.assertEqual(len(placed), len(states),
                         f"{len(states)} Items were built, one per work-engine "
                         f"state, and the sections hold {len(placed)}")
        self.assertEqual(board["unplaced"], [])

    def test_the_live_fixture_places_every_row_it_declares(self):
        loaded = chain.load_projects(boards_root=SAMPLE / "boards")
        board = chain.compose_board(
            contract=CONTRACT, projects=loaded,
            records_root=SAMPLE / "records", workspace_root=SAMPLE,
            today_by_project={"sample-alpha":
                              chain.load_today(chain.today_path(SAMPLE / "records",
                                                                "sample-alpha")),
                              "sample-beta":
                              chain.load_today(chain.today_path(SAMPLE / "records",
                                                                "sample-beta"))})
        self.assertEqual(board["unplaced"], [])
        # The section counts SUM to the Item count, which is what the
        # `today: false` filter buys: without it the Item a person selected
        # would be placed twice.
        placed = [r["id"] for s in board["sections"] for r in s["rows"]]
        self.assertEqual(len(placed), board["counts"]["items"])
        self.assertEqual(len(set(placed)), len(placed))
        # Every Item the projections declare is on the board. Read from the
        # projections rather than hard-coded, so adding a counterexample to the
        # fixture does not leave a number here that has to be edited by hand.
        declared = {i["id"] for project in loaded
                    for i in (project.get("document") or {}).get("items") or []}
        self.assertEqual(set(placed), declared)

    def test_a_state_no_section_names_is_reported_and_not_filed_under_待排期(self):
        """The new-state case, which is the one this machinery exists for.

        Dropping it into 待排期 would print in-flight work under "waiting to be
        scheduled" — the exact inversion — and nothing downstream could tell.
        """
        board = None
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "boards" / "px").mkdir(parents=True)
            (root / "records" / "projects").mkdir(parents=True)
            (root / "workspace").mkdir()
            (root / "boards" / "px" / "current.json").write_text(json.dumps(
                a_projection("px", [an_item("XX-1", state="SOMETHING_NEW")])),
                encoding="utf-8")
            projects = chain.load_projects(boards_root=root / "boards")
            board = chain.compose_board(
                contract=CONTRACT, projects=projects,
                records_root=root / "records", workspace_root=root / "workspace")
        self.assertEqual([r["id"] for r in board["unplaced"]], ["XX-1"])
        self.assertIn("SOMETHING_NEW", board["unplaced"][0]["why"])
        # ...and NOT in 待排期, which is where a fallback bucket would have put it.
        backlog = next(s for s in board["sections"] if s["id"] == "backlog")
        self.assertEqual(backlog["rows"], [])


# ---------------------------------------------------------------------------
# 别名是同一个项目的第二个名字，不是第二个项目。
# ---------------------------------------------------------------------------

class AnAliasIsASecondNameAndNotASecondProject(unittest.TestCase):
    """`litellm-observability` 是 `litellm` 的另一个名字（见 `projects.yaml`）。

    折叠本身很短，值得测的是它的两个下游后果：读两次同一个投影会让同一个 Item
    在板上出现两行，而重复 ID 在 `select_focus` 那里是抛错的 —— 一件看起来只是
    「多一行」的事，实际是整个周期算不出来。所以这里测的是**行的数量**，不是
    字典的内容。
    """

    def _fixture(self, root: Path, names) -> None:
        for name in names:
            out = root / "boards" / name / "current.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(
                a_projection(name, [an_item(f"{name.upper()[:3]}-0001")])),
                encoding="utf-8")
        (root / "records" / "projects").mkdir(parents=True, exist_ok=True)
        (root / "workspace").mkdir(parents=True, exist_ok=True)

    def test_two_names_for_one_project_read_one_projection(self):
        aliases = {"obs": {"project": "litellm", "declared_by": "README.md"}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root, ["litellm", "marketops"])
            loaded = chain.load_projects(
                boards_root=root / "boards",
                projects=["litellm", "marketops", "obs"], aliases=aliases)
            board = chain.compose_board(
                contract=CONTRACT, projects=loaded,
                records_root=root / "records", workspace_root=root / "workspace")
        # 三个名字，两份投影。多出来的那一行会让同一个 Item 出现两次。
        self.assertEqual([p["project"] for p in loaded], ["litellm", "marketops"])
        self.assertEqual(board["counts"]["projects_read"], 2)
        rows = [r["id"] for s in board["sections"] for r in s["rows"]]
        self.assertEqual(len(rows), len(set(rows)),
                         "同一个 Item 出现在两行里，周期就算不出来了")
        # 而且那一行说得出它同时回答了哪个名字 —— 「点了三个、拿到两行」的差
        # 不能由这一步替调用者咽下去。
        answered = {p["project"]: p["also_named"] for p in board["projects"]}
        self.assertEqual(answered, {"litellm": ["obs"], "marketops": []})

    def test_a_name_that_is_already_the_target_is_read_once(self):
        """点两次同一个名字，和点一个名字加一个别名，是同一件事。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root, ["litellm"])
            loaded = chain.load_projects(boards_root=root / "boards",
                                         projects=["litellm", "litellm"], aliases={})
        self.assertEqual([p["project"] for p in loaded], ["litellm"])

    def test_a_broken_alias_does_not_answer_for_another_project(self):
        """别名指向一个没注册的名字，是一个坏掉的绑定，不是「换个名字」。

        它的表现是一行 UNREADABLE，行名是**注册表让它去读的那个名字**，而
        `also_named` 说得出是谁把自己带到这里来的 —— 于是「这个别名指错了」
        在看得见的地方，而不是被猜成别的项目。这条测的正是那个「不猜」：
        盘上有 `litellm` 的投影，而这个别名一个字段都不提它。
        """
        aliases = {"obs": {"project": "nobody-registered-this"}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root, ["litellm"])
            loaded = chain.load_projects(boards_root=root / "boards",
                                         projects=["obs"], aliases=aliases)
        self.assertEqual([p["project"] for p in loaded], ["nobody-registered-this"])
        self.assertEqual(loaded[0]["state"], chain.UNREADABLE)
        self.assertEqual(loaded[0]["also_named"], ["obs"])
        self.assertNotIn("litellm", [p["project"] for p in loaded],
                         "别名指错时读到了盘上别的项目：这就是那个不出声的错")

    def test_the_record_names_the_target_and_not_the_alias(self):
        names, renamed = chain.resolve_aliases(
            ["obs", "litellm", "marketops"],
            {"obs": {"project": "litellm", "declared_by": "README.md"}})
        self.assertEqual(names, ["litellm", "marketops"])
        self.assertEqual(renamed, {"litellm": ["obs"]})

    def test_the_glob_branch_is_not_told_about_aliases(self):
        """不许别名在走磁盘的那条分支上凭空造出一个项目。

        另一条分支列的是**盘上有什么**，而一个叫 `obs` 的目录不是任何注册项
        能造出来的。在那里应用别名，等于用一个回答去填一个没人问过的问题 ——
        和凭空发明一个项目无法区分。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root, ["litellm"])
            loaded = chain.load_projects(
                boards_root=root / "boards",
                aliases={"obs": {"project": "litellm"}})
        self.assertEqual([p["project"] for p in loaded], ["litellm"])


# ---------------------------------------------------------------------------
# The locator is resolved, never recomposed.
# ---------------------------------------------------------------------------

class TheCardLocatorIsResolvedAndNeverAssumed(unittest.TestCase):
    def test_a_relative_locator_is_relative_to_the_workspace_root(self):
        """...and the answer CHANGES with the workspace root, which is how a
        reader can tell it is being used at all. A relative locator resolved
        against the cwd would silently join a different set of cards, and every
        row would look complete.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = root / "records"
            (records / "projects" / "px" / "items" / "IT-1").mkdir(parents=True)
            (root / "workspace" / "records" / "projects" / "px" / "items"
             / "IT-1").mkdir(parents=True)
            (root / "workspace" / "records" / "projects" / "px" / "items"
             / "IT-1" / "card.yaml").write_text("id: IT-1\n", encoding="utf-8")
            resolved = chain.resolve_card_path(
                locator="records/projects/px/items/IT-1/card.yaml", project="px",
                item_id="IT-1", records_root=records,
                workspace_root=root / "workspace")
            self.assertEqual(resolved["how"], "DECLARED")
            self.assertEqual(resolved["path"],
                             root / "workspace" / "records" / "projects" / "px"
                             / "items" / "IT-1" / "card.yaml")
            # The records root alone does NOT resolve it, which is what a
            # composed path would have claimed.
            self.assertFalse((records / "px" / "items" / "IT-1"
                              / "card.yaml").is_file())

    def test_a_foreign_absolute_locator_is_rebased_and_says_so(self):
        """The declaration outlived the machine it described.

        Every litellm locator on this node is of this shape, because the
        authority wrote them on Beijing. Refusing to rebase would hide eight
        readable cards; rebasing silently would hide a pointer that is still
        wrong — so the row carries the foreign root AND the path it used.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = (root / "workspace" / "records" / "projects" / "px"
                      / "items" / "IT-1")
            target.mkdir(parents=True)
            (target / "card.yaml").write_text("id: IT-1\n", encoding="utf-8")
            resolved = chain.resolve_card_path(
                locator="/opt/applications/data/home-hwz/workspace/records/"
                        "projects/px/items/IT-1/card.yaml",
                project="px", item_id="IT-1", records_root=root / "records",
                workspace_root=root / "workspace")
            self.assertEqual(resolved["how"], "REBASED")
            self.assertEqual(resolved["path"], target / "card.yaml")
            # Both facts are in the answer: the locator verbatim, and the root
            # it disagreed with.
            self.assertIn("/opt/applications/data/home-hwz", resolved["locator"])
            self.assertIn("/opt/applications/data/home-hwz", resolved["why"])
            self.assertIn(str(target / "card.yaml"), resolved["why"])
            card = chain.load_card(resolved, "IT-1")
            self.assertEqual(card["state"], "PRESENT")
            self.assertEqual(card["how"], "REBASED")

    def test_a_locator_that_resolves_nowhere_is_a_broken_pointer(self):
        """NOT "this Item has no card".

        Those call for different actions — one is a card to write and the other
        is a pointer to fix — so the row keeps them apart in `how` and in the
        sentence, even though both leave the card absent.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resolved = chain.resolve_card_path(
                locator="/somewhere/on/a/machine/that/is/gone/card.yaml",
                project="px", item_id="IT-1", records_root=root / "records",
                workspace_root=root / "workspace")
            self.assertEqual(resolved["how"], "UNRESOLVED")
            card = chain.load_card(resolved, "IT-1")
            self.assertEqual(card["state"], "MISSING")
            self.assertEqual(card["how"], "UNRESOLVED")
            self.assertIn("resolves to nothing", card["why"])
            # The locator is echoed verbatim, so a reader can see the string
            # that was wrong rather than only the conclusion about it.
            self.assertEqual(card["locator"],
                             "/somewhere/on/a/machine/that/is/gone/card.yaml")

    def test_no_locator_falls_back_to_the_conventional_path_and_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conventional = (chain.project_dir(root / "records", "px") / "items"
                            / "IT-1" / "card.yaml")
            conventional.parent.mkdir(parents=True)
            conventional.write_text("id: IT-1\n", encoding="utf-8")
            resolved = chain.resolve_card_path(
                locator=None, project="px", item_id="IT-1",
                records_root=root / "records", workspace_root=root / "workspace")
            self.assertEqual(resolved["how"], "CONVENTIONAL")
            self.assertEqual(resolved["path"], conventional)
            self.assertIn("no `card_locator`", resolved["why"])

    def test_a_card_that_declares_another_id_is_unreadable(self):
        """The fixture's `SAMP-0104` is this case on purpose.

        Joining it would put one Item's title on another Item's state.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            card = (chain.project_dir(root / "records", "px") / "items" / "IT-1"
                    / "card.yaml")
            card.parent.mkdir(parents=True)
            card.write_text("id: IT-9999\ntitle: somebody else\n",
                            encoding="utf-8")
            resolved = chain.resolve_card_path(
                locator=None, project="px", item_id="IT-1",
                records_root=root / "records", workspace_root=root / "workspace")
            answer = chain.load_card(resolved, "IT-1")
            self.assertEqual(answer["state"], "UNREADABLE")
            self.assertIsNone(answer["document"])
            self.assertIn("IT-9999", answer["why"])

    def test_the_fixtures_malformed_card_is_reported_as_unreadable(self):
        loaded = chain.load_projects(boards_root=SAMPLE / "boards")
        board = chain.compose_board(
            contract=CONTRACT, projects=loaded,
            records_root=SAMPLE / "records", workspace_root=SAMPLE)
        rows = {r["id"]: r for s in board["sections"] for r in s["rows"]}
        self.assertEqual(rows["SAMP-0104"]["card"]["state"], "UNREADABLE")
        self.assertEqual(board["counts"]["cards_unreadable"], 1)
        # ...and the other nine cards DID resolve, so the count is a fact about
        # one card and not about the fixture failing to load.
        self.assertEqual(board["counts"]["cards_missing"], 0)


class TheItemDirectoryIsTheCardsOwn(unittest.TestCase):
    def test_documents_are_looked_for_beside_the_card_not_recomposed(self):
        """A brief next to a card is one Item's brief; the same filename under a
        directory this module guessed is a different document.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            elsewhere = (root / "workspace" / "records" / "projects" / "px"
                         / "items" / "IT-1")
            elsewhere.mkdir(parents=True)
            (elsewhere / "card.yaml").write_text("id: IT-1\n", encoding="utf-8")
            (elsewhere / "brief.md").write_text("# a brief\n", encoding="utf-8")
            out = root / "boards" / "px" / "current.json"
            out.parent.mkdir(parents=True)
            out.write_text(json.dumps(a_projection("px", [an_item(
                "IT-1",
                card_locator="records/projects/px/items/IT-1/card.yaml")])),
                encoding="utf-8")
            projects = chain.load_projects(boards_root=root / "boards")
            board = chain.compose_board(
                contract=CONTRACT, projects=projects,
                records_root=root / "records", workspace_root=root / "workspace")
        row = board["sections"][1]["rows"][0]
        brief = next(d for d in row["documents"] if d["document"] == "brief.md")
        self.assertEqual(brief["state"], "PRESENT")
        self.assertEqual(Path(brief["path"]), elsewhere / "brief.md")


class ADocumentResolvesAsAFileOrADirectory(unittest.TestCase):
    """`design/` is what this node's records hold; `design.md` is not.

    A first draft named the `.md` spelling and reported every design, plan and
    report on the node as unwritten while the files sat on the disk.
    """

    def _rules(self):
        return {r["name"]: r for r in chain._document_rules(CONTRACT)}

    def test_a_directory_counts_only_when_it_holds_something(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = Path(tmp)
            (item / "design").mkdir()
            path, how, count = chain._resolve_document(item, self._rules()["design"])
            self.assertIsNone(path, "an empty `design/` is a directory somebody "
                                    "made, which is not a design somebody wrote")
            (item / "design" / "A.md").write_text("x", encoding="utf-8")
            (item / "design" / "B.md").write_text("x", encoding="utf-8")
            path, how, count = chain._resolve_document(item, self._rules()["design"])
            self.assertEqual((path, how, count), (item / "design", "directory", 2))

    def test_the_suffixed_spelling_still_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = Path(tmp)
            (item / "design.md").write_text("x", encoding="utf-8")
            path, how, _ = chain._resolve_document(item, self._rules()["design"])
            self.assertEqual((path, how), (item / "design.md", "suffixed-file"))

    def test_an_unknown_resolution_rule_is_refused(self):
        """Reporting an unknown rule as absent would call a document missing
        because its spelling was not understood — which is the defect the rule
        list exists to prevent, reintroduced one level up.
        """
        broken = dict(CONTRACT)
        broken["item_documents"] = [{"name": "design",
                                     "resolved_by": ["vibes"],
                                     "required_at": ["ACTIVE"]}]
        with self.assertRaises(chain.Refused) as caught:
            chain._document_rules(broken)
        self.assertIn("vibes", str(caught.exception))

    def test_an_empty_document_set_is_refused(self):
        broken = dict(CONTRACT)
        broken["item_documents"] = []
        with self.assertRaises(chain.Refused):
            chain._document_rules(broken)


# ---------------------------------------------------------------------------
# What is IN a document. Three of the four answers are not defects.
# ---------------------------------------------------------------------------

class ADocumentIsCheckedForContentAndNeverForStyle(unittest.TestCase):
    """The second version of this checker, and the defect that produced it.

    The first version reported a document `INCOMPLETE` whenever it lacked the
    headings `document-skeletons.yaml` proposes, and then it was pointed at
    this node — where 21 real `brief.md` files use headings of their own and
    none uses the proposed ones. It called all 21 broken. A rule invented in a
    contract, rendered as a fact about somebody's work, with the alarm on every
    row, is how a board becomes one nobody reads.

    So `DIVERGES` is a difference and `EMPTY` is the defect, and the tests below
    hold that line in both directions: a well-structured document that chose its
    own sections must NOT be counted as a gap, and a blank file must be.
    """

    SKELETON = {"id": "brief", "sections": [
        {"heading": "需求", "because": "x"},
        {"heading": "为什么现在", "because": "x"},
        {"heading": "验收", "because": "x"}]}

    #: Taken from a real brief on this node, headings and all.
    A_REAL_BRIEF = """# LIT-0201 · 需求简报

## 定性
这是一条需求，不是缺陷。

## 背景与问题陈述
上游把一个已归档的来源重新投递了两次。

## 判据与证据
判据：同一个来源 id 同一天只产出一条候选。
"""

    def test_a_document_with_its_own_headings_is_not_a_gap(self):
        answer = chain.check_skeleton(self.A_REAL_BRIEF, self.SKELETON)
        self.assertEqual(answer["state"], "DIVERGES")
        # Both halves, because either alone is a sentence a reader cannot act
        # on: "missing three" invites fixing a document that is not broken, and
        # "has headings" hides that the proposed shape was not followed.
        self.assertEqual(answer["missing"], ["需求", "为什么现在", "验收"])
        self.assertIn("定性", answer["own_headings"])
        self.assertNotIn("INCOMPLETE", answer["state"])

    def test_a_blank_document_is_the_one_defect(self):
        for blank in ("", "\n\n", "   \n\t\n"):
            answer = chain.check_skeleton(blank, self.SKELETON)
            self.assertEqual(answer["state"], "EMPTY", repr(blank))

    def test_a_document_carrying_the_declared_headings_matches(self):
        text = "## 需求\nx\n## 为什么现在\ny\n## 验收\nz\n"
        answer = chain.check_skeleton(text, self.SKELETON)
        self.assertEqual(answer["state"], "MATCHES")
        self.assertEqual(answer["missing"], [])
        self.assertEqual(len(answer["found"]), 3)

    def test_a_directory_is_not_checked_rather_than_judged(self):
        """`design/` holds a set of documents written for different reasons.

        Joining them to look for headings would be a claim this function cannot
        make, and reporting the result as a gap would be that claim with an
        alarm attached.
        """
        answer = chain.check_skeleton(None, self.SKELETON)
        self.assertEqual(answer["state"], "NOT_CHECKED")
        self.assertIn("not a check that failed", answer["why"])

    def test_a_document_nothing_declares_is_not_checked(self):
        answer = chain.check_skeleton("# anything\n", None)
        self.assertEqual(answer["state"], "NOT_CHECKED")
        self.assertEqual(answer["own_headings"], [])

    def test_every_answer_carries_the_same_keys(self):
        """A consumer reading `own_headings` off one state and not another gets
        an `AttributeError` only on the rows that happen to differ.
        """
        keys = set()
        for answer in (chain.check_skeleton(None, self.SKELETON),
                       chain.check_skeleton("", self.SKELETON),
                       chain.check_skeleton(self.A_REAL_BRIEF, self.SKELETON),
                       chain.check_skeleton("## 需求\n", self.SKELETON),
                       chain.check_skeleton("# x\n", None)):
            keys.add(frozenset(answer))
        self.assertEqual(len(keys), 1, keys)

    def test_the_board_counts_a_diverging_document_and_an_empty_one_apart(self):
        """The count is where the false alarm would land: a board whose 缺文档
        column included every document that chose its own sections would be
        amber on every row of a node where the documents are fine.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = (root / "workspace" / "records" / "projects" / "px"
                    / "items" / "IT-1")
            item.mkdir(parents=True)
            (item / "card.yaml").write_text("id: IT-1\n", encoding="utf-8")
            (item / "brief.md").write_text(self.A_REAL_BRIEF, encoding="utf-8")
            (item / "design.md").write_text("## 现状\nx\n## 方案\ny\n"
                                            "## 影响面\nz\n## 风险\nw\n",
                                            encoding="utf-8")
            (item / "plan.md").write_text("   \n", encoding="utf-8")
            out = root / "boards" / "px" / "current.json"
            out.parent.mkdir(parents=True)
            out.write_text(json.dumps(a_projection("px", [an_item(
                "IT-1", state="ACTIVE",
                card_locator="records/projects/px/items/IT-1/card.yaml")])),
                encoding="utf-8")
            board = chain.compose_board(
                contract=CONTRACT,
                projects=chain.load_projects(boards_root=root / "boards"),
                records_root=root / "records",
                workspace_root=root / "workspace",
                skeletons=chain.load_skeletons(
                    REPO / "intake" / "document-skeletons.yaml"))
        counts = board["counts"]
        self.assertEqual(counts["documents_missing"], 0)
        self.assertEqual(counts["documents_empty"], 1, "plan.md is whitespace")
        self.assertEqual(counts["documents_diverging"], 1, "brief.md chose its own")

    def test_the_fixture_exercises_every_document_state_and_every_shape(self):
        """A fixture that quietly loses its counterexamples stops testing the
        thing it was written for, and nothing about a green run would say so.
        `SAMP-0002` has no plan at all (`MISSING`), `SAMP-0007` has a plan
        holding two newlines (`EMPTY`), `SAMP-0004`'s brief uses the proposed
        headings (`MATCHES`), and every other brief is written and chooses its
        own (`DIVERGES`).
        """
        board = chain.compose_board(
            contract=CONTRACT,
            projects=chain.load_projects(boards_root=SAMPLE / "boards"),
            records_root=SAMPLE / "records", workspace_root=SAMPLE,
            skeletons=chain.load_skeletons(
                REPO / "intake" / "document-skeletons.yaml"))
        # The two vocabularies are deliberately different and this test was
        # written blending them: `d["state"]` says whether a document is
        # PRESENT/MISSING/NOT_YET_REQUIRED/UNREADABLE, and
        # `d["skeleton"]["state"]` says what is IN it. `EMPTY` belongs to the
        # second and cannot appear in the first.
        present = {d["state"] for s in board["sections"] for r in s["rows"]
                   for d in r["documents"]}
        for state in ("PRESENT", "MISSING", "NOT_YET_REQUIRED"):
            self.assertIn(state, present)
        shapes = {c.get("state") for s in board["sections"] for r in s["rows"]
                  for c in [(d.get("skeleton") or {}) for d in r["documents"]]}
        for shape in ("MATCHES", "DIVERGES", "EMPTY", "NOT_CHECKED"):
            self.assertIn(shape, shapes)
        self.assertEqual(board["counts"]["documents_empty"], 1,
                         "the fixture's one blank file")
        self.assertGreaterEqual(board["counts"]["documents_diverging"], 1)

    def test_a_document_whose_shape_is_proposed_is_not_a_schema_this_repo_owns(self):
        """`document-skeletons.yaml` says out loud that it is unapproved."""
        skeletons = chain.load_skeletons(
            REPO / "intake" / "document-skeletons.yaml")
        self.assertEqual(skeletons["status"], "PROPOSED_NOT_APPROVED")
        self.assertIn("until_approved", skeletons)

    def test_every_document_a_skeleton_names_is_a_hop_the_chain_has(self):
        """A skeleton for a hop that does not exist is a shape nothing writes."""
        skeletons = chain.load_skeletons(
            REPO / "intake" / "document-skeletons.yaml")
        hops = {h["id"] for h in CONTRACT["chain"]}
        named = {d["id"]: d.get("hop") for d in skeletons["documents"]}
        self.assertTrue(named)
        for document_id, hop in named.items():
            self.assertIn(hop, hops,
                          f"`{document_id}` is produced by hop {hop!r}, which "
                          f"`workchain-contract.yaml#chain` does not have")

    def test_the_item_documents_have_a_skeleton_when_they_are_a_single_file(self):
        """A directory-shaped document (`design/`) has no skeleton, on purpose:
        there is no single file whose headings could be checked.
        """
        skeletons = chain.load_skeletons(
            REPO / "intake" / "document-skeletons.yaml")
        for rule in chain._document_rules(CONTRACT):
            if rule["resolved_by"] == ["exact-file"]:
                self.assertIsNotNone(
                    chain.skeleton_of(skeletons, rule["name"]),
                    f"`{rule['name']}` resolves to exactly one file, so a "
                    f"skeleton can describe it — nothing declares one")


# ---------------------------------------------------------------------------
# 理解变化. Four scopes under `records/`, and not one file inside a project.
# ---------------------------------------------------------------------------

class TheUnderstandingLayerIsUnderRecordsAndNotInsideAProject(unittest.TestCase):
    """This was declared wrong first, the same way the paths were.

    `document-skeletons.yaml` named
    `records/projects/<project>/context/understanding.md`. No such file exists
    on this node and none should: the layer is
    `records/information/understanding/` with four scopes, under `records/`
    beside `projects/` rather than inside one, because understanding is a
    property of what is understood and not of whoever is carrying the work.
    """

    SKELETONS = chain.load_skeletons(
        REPO / "intake" / "document-skeletons.yaml")

    def test_the_declared_root_is_the_one_this_node_has(self):
        entry = chain.skeleton_of(self.SKELETONS, "understanding")
        self.assertEqual(entry["root"], "records/information/understanding")
        self.assertEqual(entry["scopes"][0]["id"], "projects")
        # ...and the four scopes are the four the layer actually holds.
        self.assertEqual([x["id"] for x in entry["scopes"]],
                         ["projects", "topics", "people", "responsibilities"])

    def test_the_fixture_counts_its_four_scopes(self):
        answer = chain.understanding_coverage(
            records_root=SAMPLE / "records", skeletons=self.SKELETONS)
        self.assertEqual(answer["state"], "PRESENT")
        counts = {row["scope"]: row["documents"] for row in answer["scopes"]}
        self.assertEqual(counts, {"projects": 2, "topics": 1, "people": 1,
                                  "responsibilities": 1})
        self.assertEqual(answer["undeclared_scopes"], [])

    def test_the_traceability_rule_is_counted_and_never_graded(self):
        """`understanding/README.md` states the rule; this module did not.

        So the answer is a count of documents that carry `## Sources`, and the
        people and responsibilities cards — which do not, on this node or in
        the fixture — are not a defect count.
        """
        answer = chain.understanding_coverage(
            records_root=SAMPLE / "records", skeletons=self.SKELETONS)
        rows = {row["scope"]: row for row in answer["scopes"]}
        self.assertEqual(rows["projects"]["with_sources"], 2)
        self.assertEqual(rows["people"]["with_sources"], 0)
        # A document without the heading does NOT change the row's state: the
        # scope is PRESENT either way, and the count is a number beside it
        # rather than a verdict replacing it.
        self.assertEqual(rows["people"]["state"], "PRESENT")
        self.assertEqual({row["state"] for row in answer["scopes"]}, {"PRESENT"})
        # No field here is named after a defect, a grade or a completion.
        for key in ("state", "documents", "with_sources"):
            self.assertIn(key, rows["people"])
        self.assertIn("counted and not graded", answer["traceability_because"])

    def test_a_scope_nobody_declared_is_named_rather_than_dropped(self):
        """A scope somebody adds is a scope this map stops being complete about.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "information" / "understanding"
            (root / "projects").mkdir(parents=True)
            (root / "projects" / "x.md").write_text("# x\n", encoding="utf-8")
            (root / "incidents").mkdir()
            answer = chain.understanding_coverage(
                records_root=Path(tmp), skeletons=self.SKELETONS)
        self.assertEqual(answer["undeclared_scopes"], ["incidents"])

    def test_a_missing_layer_is_NOT_FOUND_and_not_an_empty_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            answer = chain.understanding_coverage(
                records_root=Path(tmp), skeletons=self.SKELETONS)
        self.assertEqual(answer["state"], "NOT_FOUND")
        self.assertEqual([row["state"] for row in answer["scopes"]],
                         ["NOT_FOUND"] * 4)
        self.assertEqual([row["documents"] for row in answer["scopes"]],
                         [0, 0, 0, 0])

    def test_no_declared_scopes_is_refused(self):
        """Reporting four zeroes would say the layer is empty, which is a claim
        about the world made by a program with nothing to look for.
        """
        with self.assertRaises(chain.Refused) as caught:
            chain.understanding_coverage(records_root=SAMPLE / "records",
                                         skeletons={"documents": []})
        self.assertIn("document-skeletons.yaml", str(caught.exception))

    def test_the_layer_is_not_an_item_document(self):
        """It is not delivered by an Item and must not appear in an Item's
        document list — an Item that has to produce the project's understanding
        would make one Item's report the whole project's knowledge.
        """
        names = [rule["name"] for rule in chain._document_rules(CONTRACT)]
        self.assertNotIn("understanding", names)
        self.assertNotIn("daily-report", names)
        # ...and the reason it is absent is that its scope is wider than one
        # Item, which the skeleton states rather than leaving to be inferred.
        entry = chain.skeleton_of(self.SKELETONS, "understanding")
        self.assertEqual(entry["scope"], "node")


# ---------------------------------------------------------------------------
# Refusals arrive as sentences, and never as a traceback.
# ---------------------------------------------------------------------------

class ARefusalIsAReadableSentence(unittest.TestCase):
    """`Refused` is how this module says "I will not answer this, and here is
    why". Every one of those messages was written to be read — an unknown
    resolution rule, a contract naming no sections, a skeleton file present but
    naming no scopes — and each reaches a person as a traceback unless
    something catches it. A traceback carries the same information with the
    explanation buried under a stack, and it never names the reader's file.
    """

    CLI = _load("serveros_runtime_cli", REPO / "runtime" / "cli.py")

    def test_a_refusal_becomes_the_sentence_and_a_fault_exit(self):
        def refuses(_args):
            raise chain.Refused("the resolution rule `vibes` is not one this "
                                "contract declares")
        import contextlib, io
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = self.CLI._workchain_refusals(refuses)(None)
        self.assertEqual(code, self.CLI.EXIT_FAULT)
        self.assertIn("vibes", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_a_command_that_answers_is_untouched(self):
        code = self.CLI._workchain_refusals(lambda _args: self.CLI.EXIT_OK)(None)
        self.assertEqual(code, self.CLI.EXIT_OK)

    def test_the_wrapper_is_wired_to_every_verb_that_can_refuse(self):
        """Registered in `main`'s parser rather than wrapped ad hoc, so a verb
        added later is one edit away from being covered and the omission is
        visible in the file."""
        source = (REPO / "runtime" / "cli.py").read_text(encoding="utf-8")
        for name in ("board", "item", "today", "skeleton", "coverage"):
            self.assertIn(f"func=_workchain_refusals(cmd_work_{name})", source)


class ADeclarationThatIsAbsentIsNotAnError(unittest.TestCase):
    """The skeleton file is a PROPOSAL. A caller who did not point at one has
    not made a mistake, and a traceback is the loudest possible way to say the
    quietest possible thing.
    """

    def test_no_skeleton_file_is_not_checked_and_not_a_refusal(self):
        answer = chain.understanding_coverage(
            records_root=SAMPLE / "records", skeletons=None)
        self.assertEqual(answer["state"], "NOT_CHECKED")
        self.assertEqual(answer["scopes"], [])
        self.assertIsNone(answer["denominator"])
        self.assertIn("not an empty layer", answer["why"])

    def test_a_skeleton_that_declares_no_scopes_is_refused(self):
        """The opposite case: a file IS present and says nothing about the
        layer, which would render as a complete map of nothing.
        """
        with self.assertRaises(chain.Refused):
            chain.understanding_coverage(
                records_root=SAMPLE / "records",
                skeletons={"documents": [{"id": "brief"}]})


# ---------------------------------------------------------------------------
# The authored selection: a decision, and never a derived file.
# ---------------------------------------------------------------------------

class TheAuthoredSelection(unittest.TestCase):
    def test_the_writer_and_the_reader_agree_on_the_path(self):
        """They did not, and the failure was silent.

        `write_today` wrote `<records>/projects/<p>/board/today.yaml` and the
        CLI read `<records>/<p>/board/today.yaml`. The board then reported
        "nobody has chosen" for a project where somebody had — which is the one
        collapse this module names in its own docstring as forbidden.
        """
        with tempfile.TemporaryDirectory() as tmp:
            records = Path(tmp) / "records"
            written = chain.write_today(records_root=records, project="px",
                                        items=["IT-1"], date="2026-01-01",
                                        actor="test")
            self.assertEqual(Path(written["path"]),
                             chain.today_path(records, "px"))
            read = chain.load_today(chain.today_path(records, "px"))
            self.assertEqual(read["state"], "SELECTED")
            self.assertEqual(read["items"], ["IT-1"])

    def test_an_absent_selection_is_unproduced_and_not_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            answer = chain.load_today(Path(tmp) / "today.yaml")
        self.assertEqual(answer["state"], "UNPRODUCED")
        self.assertIn("no selection was recorded", answer["why"])

    def test_a_selection_naming_nothing_is_empty_and_not_unproduced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "today.yaml"
            path.write_text("schema: serveros/today.v1\nitems: []\n",
                            encoding="utf-8")
            answer = chain.load_today(path)
        self.assertEqual(answer["state"], "EMPTY")
        self.assertIn("chose nothing", answer["why"])

    def test_one_project_without_a_selection_makes_the_merge_partial(self):
        """Not SELECTED and not UNPRODUCED. Two projects, one choice.

        Collapsing this to SELECTED would say a person chose for both; to
        UNPRODUCED would say they chose for neither.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "boards").mkdir()
            (root / "records" / "projects").mkdir(parents=True)
            (root / "workspace").mkdir()
            for name in ("pa", "pb"):
                out = root / "boards" / name / "current.json"
                out.parent.mkdir()
                out.write_text(json.dumps(a_projection(name, [an_item("IT-1")])),
                               encoding="utf-8")
                (root / "records" / "projects" / name / "items" / "IT-1").mkdir(
                    parents=True)
                (root / "records" / "projects" / name / "items" / "IT-1"
                 / "card.yaml").write_text("id: IT-1\n", encoding="utf-8")
            chain.write_today(records_root=root / "records", project="pa",
                              items=["IT-1"], date="2026-01-01", actor="test")
            projects = chain.load_projects(boards_root=root / "boards")
            board = chain.compose_board(
                contract=CONTRACT, projects=projects,
                records_root=root / "records", workspace_root=root / "workspace",
                today_by_project={
                    "pa": chain.load_today(chain.today_path(root / "records", "pa")),
                    "pb": chain.load_today(chain.today_path(root / "records", "pb")),
                })
        self.assertEqual(board["today"]["state"], "PARTIAL")
        self.assertIn("pb", board["today"]["why"])
        self.assertEqual(board["today"]["items"], ["IT-1"])


# ---------------------------------------------------------------------------
# The two counterexamples the board must not confuse.
# ---------------------------------------------------------------------------

class DeliveredIsNotCompletion(unittest.TestCase):
    def test_an_unmerged_pull_request_is_delivered_and_not_done(self):
        """The fixture's `SAMP-0003`: pushed, PR open, merge not done.

        §6's 已完成 means the Item's own agreed acceptance was met — not "the
        run exited 0" and not "the PR was opened". A board that filed this
        under 已完成 would report a merge that has not happened.
        """
        loaded = chain.load_projects(boards_root=SAMPLE / "boards")
        board = chain.compose_board(
            contract=CONTRACT, projects=loaded,
            records_root=SAMPLE / "records", workspace_root=SAMPLE)
        sections = {s["id"]: [r["id"] for r in s["rows"]]
                    for s in board["sections"]}
        self.assertIn("SAMP-0003", sections["delivered"])
        self.assertNotIn("SAMP-0003", sections["done"])

    def test_a_closed_item_with_no_report_is_still_undecided(self):
        """`CLOSED` with no report contradicts the contract's own rule, and the
        Item's chain says so rather than counting it complete.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "boards" / "px" / "current.json"
            out.parent.mkdir(parents=True)
            out.write_text(json.dumps(a_projection("px", [an_item(
                "IT-1", state="CLOSED", outcome="SUCCESS")])), encoding="utf-8")
            (root / "records" / "projects").mkdir(parents=True)
            (root / "workspace").mkdir()
            (root / "records" / "projects" / "px" / "items" / "IT-1").mkdir(
                parents=True)
            (root / "records" / "projects" / "px" / "items" / "IT-1"
             / "card.yaml").write_text("id: IT-1\n", encoding="utf-8")
            projects = chain.load_projects(boards_root=root / "boards")
            item = chain.compose_item(
                contract=CONTRACT, records_root=root / "records",
                workspace_root=root / "workspace", project="px", item_id="IT-1",
                projects=projects)
        self.assertEqual(item["state"], "CLOSED")
        self.assertIn("report", [u["document"] for u in item["undecided"]])
        # And the boundary says the same thing in the form code reads: the Item
        # claims completion, `outcome` IS set, and what is missing is named.
        # Before this field existed the only reader of the same rule was this
        # assertion's neighbour above, which no caller consulted.
        self.assertEqual(item["completion"]["state"], chain.OPEN)
        self.assertEqual(item["completion"]["missing_fields"], [])
        self.assertEqual(item["completion"]["missing_documents"], ["report"])

    def test_the_contract_says_a_quay_task_is_not_an_outcome(self):
        """§6's 已完成 is one Item's acceptance. A Quay Task is a step INSIDE
        an Item, and counting it as a second outcome would make one piece of
        work look like two deliverables.
        """
        completion = CONTRACT["completion"]
        self.assertFalse(completion["quay_task_counts_as_an_outcome"])
        self.assertNotIn("DELIVERED", completion["states"])

    def test_the_sentence_and_the_lists_name_the_same_requirements(self):
        """`completion.requires` is prose and the two lists are code.

        Two spellings of one rule is exactly what this round keeps finding
        broken — a rule implemented twice cannot be killed by a single mutation
        — so keeping both is defensible only while something fails when they
        drift. This is that something, and it is deliberately one-directional:
        every requirement the LISTS name must appear in the sentence, because a
        requirement code enforces and the sentence hides would leave a reader
        unable to see why an Item they consider done is reported as owing
        something. The other direction needs the sentence to be parsed, and a
        parser here would be a third spelling rather than a check on two.
        """
        completion = CONTRACT["completion"]
        for field in completion["requires_fields"]:
            self.assertIn(field, completion["requires"], field)
        for name in completion["requires_documents"]:
            self.assertIn(name, completion["requires"], name)

    def test_a_state_that_claims_completion_is_also_tested_there(self):
        """A claim nothing tests is a declaration, not a boundary."""
        completion = CONTRACT["completion"]
        self.assertTrue(set(completion["states"]) <= set(completion["assessed_at"]))
        self.assertFalse(set(completion["states"]) & set(completion["not_completion"]))

    def test_a_cancelled_item_can_name_what_replaced_it(self):
        """取消/替代 is ONE section, and being replaced is a reference.

        Cancellation is a state work-engine already has; a replacement is not,
        so `superseded_by` is the field that lets the section say what took the
        cancelled Item's place instead of only that it stopped.
        """
        loaded = chain.load_projects(boards_root=SAMPLE / "boards")
        projects = loaded
        item = chain.compose_item(
            contract=CONTRACT, records_root=SAMPLE / "records",
            workspace_root=SAMPLE, project="sample-alpha", item_id="SAMP-0005",
            projects=projects)
        self.assertEqual(item["state"], "CANCELLED")
        self.assertEqual(item["superseded_by"], "SAMP-0006")
        # The Item that did the replacing is a row of its own, not a rename.
        replacement = chain.compose_item(
            contract=CONTRACT, records_root=SAMPLE / "records",
            workspace_root=SAMPLE, project="sample-alpha", item_id="SAMP-0006",
            projects=projects)
        self.assertEqual(replacement["state"], "READY")


# ---------------------------------------------------------------------------
# The completion boundary, one answer at a time.
# ---------------------------------------------------------------------------

def a_completion_row(state="CLOSED", outcome="SUCCESS", documents=None) -> dict:
    return {"id": "IT-1", "project": "px", "state": state, "outcome": outcome,
            "documents": ([{"document": "report", "state": chain.PRESENT}]
                          if documents is None else documents)}


def a_contract(**completion) -> dict:
    """The real contract with its `completion` block replaced, key by key."""
    block = {"states": ["CLOSED"], "requires": "outcome is set, and report.md "
             "is present", "requires_fields": ["outcome"],
             "requires_documents": ["report"], "assessed_at": ["DELIVERED",
                                                               "CLOSED"],
             "not_completion": ["DELIVERED", "CANCELLED"]}
    block.update(completion)
    return {**CONTRACT, "completion": block}


class TheCompletionBoundaryAnswersFourThings(unittest.TestCase):
    """`completion_of` over hand-built rows, so each answer is reachable alone.

    The rows are built here rather than taken from the fixture because the
    fixture exercises the four answers through real Items; what this class adds
    is that each answer can be reached WITHOUT the others being true, which is
    the only way to tell four branches from one branch and three coincidences.
    """

    def setUp(self):
        self.rules = chain._completion_rules(CONTRACT)

    def test_a_state_the_contract_does_not_test_is_not_assessed(self):
        verdict = chain.completion_of(a_completion_row(state="ACTIVE"), self.rules)
        self.assertEqual(verdict["state"], chain.NOT_ASSESSED)
        self.assertFalse(verdict["assessed"])
        self.assertIn("ACTIVE", verdict["why"])

    def test_closed_with_everything_present_is_complete(self):
        verdict = chain.completion_of(a_completion_row(), self.rules)
        self.assertEqual(verdict["state"], chain.COMPLETE)
        self.assertTrue(verdict["assessed"])

    def test_delivered_with_everything_present_is_discharged_and_not_complete(self):
        """The state the whole field exists for: delivered is not done."""
        verdict = chain.completion_of(a_completion_row(state="DELIVERED"),
                                      self.rules)
        self.assertEqual(verdict["state"], chain.DISCHARGED)
        self.assertNotEqual(verdict["state"], chain.COMPLETE)
        self.assertIn("acceptance", verdict["why"])

    def test_a_missing_document_is_open_and_the_gap_is_named(self):
        verdict = chain.completion_of(a_completion_row(documents=[
            {"document": "report", "state": chain.MISSING}]), self.rules)
        self.assertEqual(verdict["state"], chain.OPEN)
        self.assertEqual(verdict["missing_documents"], ["report"])
        self.assertEqual(verdict["missing_fields"], [])

    def test_an_empty_outcome_is_not_an_outcome(self):
        """`None`, `""` and `[]` are absences wearing a value's clothes."""
        for empty in (None, "", "   ", [], {}):
            verdict = chain.completion_of(
                a_completion_row(outcome=empty), self.rules)
            self.assertEqual(verdict["state"], chain.OPEN, repr(empty))
            self.assertEqual(verdict["missing_fields"], ["outcome"], repr(empty))

    def test_a_row_with_no_documents_list_is_not_assessed_rather_than_complete(self):
        """Silence about the documents is not evidence that they are there."""
        row = a_completion_row()
        del row["documents"]
        verdict = chain.completion_of(row, self.rules)
        self.assertEqual(verdict["state"], chain.NOT_ASSESSED)
        self.assertFalse(verdict["assessed"])
        self.assertIn("documents", verdict["why"])

    def test_a_required_document_the_row_never_mentions_is_owed_not_forgiven(self):
        """Absent and unreported are both owed, and told apart."""
        verdict = chain.completion_of(a_completion_row(documents=[]), self.rules)
        self.assertEqual(verdict["state"], chain.OPEN)
        self.assertEqual(verdict["missing_documents"], [])
        self.assertEqual(verdict["unreported_documents"], ["report"])

    def test_a_requirement_that_could_never_bite_is_refused(self):
        """A test that cannot fail is not a test.

        `report` is required at DELIVERED and CLOSED, so naming it while
        assessing a state where it is NOT_YET_REQUIRED would leave the check
        green for every Item — the defect class this whole section is about,
        arriving one level down.
        """
        rules = chain._completion_rules(a_contract(
            states=["READY"], assessed_at=["READY"]))
        with self.assertRaises(chain.Refused) as caught:
            chain.completion_of(a_completion_row(state="READY", documents=[
                {"document": "report", "state": chain.NOT_YET_REQUIRED}]),
                rules)
        self.assertIn("could never fail", str(caught.exception))

    def test_the_rules_refuse_a_boundary_nothing_tests(self):
        for block, expected in (
                ({"states": [], "assessed_at": ["CLOSED"]}, "names no state"),
                ({"states": ["CLOSED"], "assessed_at": []}, "names no state"),
                ({"states": ["CLOSED"], "assessed_at": ["DELIVERED"]},
                 "never tests there"),
                ({"not_completion": ["CLOSED"]}, "both completion and not"),
                ({"requires_fields": [], "requires_documents": []},
                 "nothing to check"),
                ({"requires_documents": ["nothing-declares-this"]},
                 "does not declare"),
        ):
            with self.subTest(block=block):
                with self.assertRaises(chain.Refused) as caught:
                    chain._completion_rules(a_contract(**block))
                self.assertIn(expected, str(caught.exception))


# ---------------------------------------------------------------------------
# A credential is never stored, and priority is never invented.
# ---------------------------------------------------------------------------

class ACredentialIsNeverStored(unittest.TestCase):
    # Assembled at runtime so this file carries no matchable credential shape
    # of its own — otherwise the fixture that proves the refusal works is
    # itself a finding when the scanner is pointed at the repository. The
    # `sk-` member below has been written this way since it was added; this
    # one was not, and `scripts/scan_secrets.py` reported it.
    PRIVATE_KEY_HEADER = "-----BEGIN " + "PRIVATE KEY-----"

    def test_a_reference_is_accepted_and_a_value_is_refused(self):
        for reference in ("vault:feishu/tokyo-reader", "feishu-reader"):
            self.assertTrue(chain._looks_like_a_reference(reference), reference)
        # A value, a bearer header, and a path INTO a vault are all refused —
        # the last because a path to a file that HOLDS a secret is one `read()`
        # away from a secret in this repository.
        for value in ("sk-" + "a" * 40, "Bearer abcdef", "token=abcdef",
                      self.PRIVATE_KEY_HEADER,
                      "vault/feishu/tokyo-reader.json",
                      # 32 hex characters with no separator: this one has no
                      # recognisable prefix at all, which is the case a
                      # prefix list cannot catch and the run rule can.
                      "vault:feishu/8f14e45fceea167a5a36dedd4bea2543",
                      "x" * 40):
            self.assertFalse(chain._looks_like_a_reference(value), value)
        # ...and a long reference spelled in WORDS is still accepted, so the
        # rule refuses blobs and not length.
        self.assertTrue(chain._looks_like_a_reference(
            "vault:feishu/2026-09-20-tokyo-reader-daily-digest"))

    def test_a_source_row_with_a_credential_value_is_refused(self):
        answer = chain.source_coverage(
            contract=CONTRACT,
            sources=[{"identity": "x", "because": "y", "refresh": "z",
                      "depth": "w", "credential_ref": "Bearer abcdef",
                      "publishable": True}])
        self.assertEqual(answer["rows"][0]["state"], "REFUSED")
        self.assertEqual(answer["coverage"]["refused"], 1)

    def test_the_fixtures_refused_row_is_refused(self):
        """The fixture keeps one good row and one bad one, so this checks that
        a refusal does not take the good row with it.
        """
        read = chain.load_sources(records_root=SAMPLE / "records",
                                  project="sample-alpha")
        self.assertEqual(read["state"], "READ")
        answer = chain.source_coverage(contract=CONTRACT, sources=read["sources"])
        self.assertEqual(answer["coverage"], {"authored": 2, "complete": 1,
                                              "refused": 1})


class APriorityIsNeverInvented(unittest.TestCase):
    def test_a_card_that_declares_no_priority_is_unproduced(self):
        """Not a level, and not a guess from `kind` or `state`.

        `intake/global_board.py` already states that introducing a priority
        field "has to be decided by a person", so a board that bucketed Items
        by a proxy would be inventing a taxonomy in the one column whose
        meaning is priority.
        """
        levels = chain._priority({"id": "IT-1", "kind": "bug"})
        for side in ("source", "personal"):
            self.assertEqual(levels[side]["state"], "UNPRODUCED")
            self.assertIn("not approved", levels[side]["why"])

    def test_a_level_outside_the_vocabulary_is_reported_and_not_coerced(self):
        levels = chain._priority({"personal_priority": "URGENT"})
        self.assertEqual(levels["personal"]["state"], "UNREADABLE")
        self.assertIn("URGENT", levels["personal"]["why"])
        # ...and the other side is unaffected: one bad field is not two.
        self.assertEqual(levels["source"]["state"], "UNPRODUCED")

    def test_no_card_on_this_node_declares_one(self):
        """The live board's 34 `UNPRODUCED` values are a fact about the field,
        not about the reader. If a card ever gains one this test says so.
        """
        loaded = chain.load_projects(boards_root=SAMPLE / "boards")
        board = chain.compose_board(
            contract=CONTRACT, projects=loaded,
            records_root=SAMPLE / "records", workspace_root=SAMPLE)
        self.assertEqual(board["counts"]["priorities_unproduced"],
                         2 * board["counts"]["items"])


# ---------------------------------------------------------------------------
# What is refused, and where the fixture is allowed to live.
# ---------------------------------------------------------------------------

class WhatIsRefused(unittest.TestCase):
    def test_a_contract_that_is_not_the_one_it_says_it_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.yaml"
            path.write_text("schema: something/else\n", encoding="utf-8")
            with self.assertRaises(chain.Refused) as caught:
                chain.load_contract(path)
        self.assertIn("something/else", str(caught.exception))

    def test_a_relative_root_is_refused_by_the_command(self):
        """Composed from the cwd, two people get two boards and neither can
        tell. The refusal is asserted against the real entry point rather than
        reimplemented here.
        """
        source = (REPO / "runtime" / "cli.py").read_text(encoding="utf-8")
        self.assertIn("must be absolute", source)
        for label in ("--boards-root", "--records-root", "--workspace-root"):
            self.assertIn(label, source, label)

    def test_the_fixture_is_not_under_a_production_root(self):
        """The fixture demonstrates `CLOSED` and it must never be read as a
        delivery. It lives in the repository, and nothing this product reads
        for real resolves into it.
        """
        resolved = SAMPLE.resolve()
        self.assertEqual(resolved, REPO / "intake" / "samples" / "workchain")
        for production in (paths_mod.Paths().root("workspace") / "records",
                           paths_mod.Paths().resolve("state/boards")["path"]):
            self.assertNotEqual(resolved, Path(production).resolve())
            self.assertNotIn(Path(production).resolve(), resolved.parents)
        readme = (SAMPLE / "README.md").read_text(encoding="utf-8")
        self.assertIn("SAMPLE", readme)
        self.assertIn("No Item here", readme)

    def test_a_projection_row_with_no_id_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "boards" / "px").mkdir(parents=True)
            (root / "records" / "projects").mkdir(parents=True)
            (root / "workspace").mkdir()
            (root / "boards" / "px" / "current.json").write_text(json.dumps(
                a_projection("px", [{"state": "ACTIVE"}])), encoding="utf-8")
            projects = chain.load_projects(boards_root=root / "boards")
            with self.assertRaises(chain.Refused) as caught:
                chain.compose_board(contract=CONTRACT, projects=projects,
                                    records_root=root / "records",
                                    workspace_root=root / "workspace")
        self.assertIn("has no id", str(caught.exception))

    def test_a_projection_that_is_not_board_v3_is_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "boards" / "px").mkdir(parents=True)
            (root / "boards" / "px" / "current.json").write_text(
                json.dumps({"schema": "serveros/board.v2", "items": []}),
                encoding="utf-8")
            projects = chain.load_projects(boards_root=root / "boards")
        self.assertEqual(projects[0]["state"], "UNREADABLE")
        self.assertIn("board.v2", projects[0]["why"])

    def test_a_missing_projection_is_not_an_empty_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects = chain.load_projects(boards_root=Path(tmp) / "boards",
                                           projects=["px"])
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["state"], "UNREADABLE")
        self.assertIn("has not written", projects[0]["why"])


# ---------------------------------------------------------------------------
# The fifth instance of the same defect: a fact about the reader's tree
# ---------------------------------------------------------------------------

def _fake_git(*, toplevel, head="headsha000000", branch="main",
              upstream_error=True, ref_exists=True, counts="0\t0",
              has_files=(), rev_of_ref="refsha000000"):
    """A git that answers exactly the questions `tree_position` asks.

    Not a repository. `tree_position` takes its git callable injected so that
    the question "is this absence the tree's" can be asked in a test without
    building one, and so that a fixture cannot accidentally start depending on
    a working copy of somebody's records.
    """
    files = set(has_files)

    def git(path, *args):
        if args[:2] == ("rev-parse", "--show-toplevel"):
            return 0, f"{toplevel}\n"
        if args == ("rev-parse", "HEAD"):
            return 0, f"{head}\n"
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return 0, f"{branch}\n"
        if args == ("rev-parse", "--abbrev-ref", "--symbolic-full-name",
                    "@{upstream}"):
            if upstream_error:
                return 128, "fatal: no upstream configured for branch 'main'\n"
            return 0, f"origin/{branch}\n"
        if args[:2] == ("rev-parse", "--verify"):
            return (0, "") if ref_exists else (1, "")
        if args[:2] == ("rev-list", "--left-right"):
            return 0, f"{counts}\n"
        if args[:1] == ("rev-parse",):
            return 0, f"{rev_of_ref}\n"
        if args[:2] == ("cat-file", "-e"):
            _ref, _, rel = args[2].partition(":")
            return (0, "") if rel in files else (128, "")
        return 127, f"unexpected call: {args}"

    return git


class ThePositionOfTheTreeAReadWasTakenFrom(unittest.TestCase):
    """`work item SRV-0011` said `brief.md MISSING`, and that was about the tree.

    On 2026-10-07 the canonical records checkout was 13 commits behind the ref
    carrying a brief that had been merged and read back, and the verb rendered
    the lag as an Item that owes a document nobody wrote. Every assertion below
    is about keeping those two apart.
    """

    def test_a_tree_with_no_upstream_declared_falls_back_and_says_so(self):
        # The exact shape of /data/home/vince/workspace/records on that day.
        position = chain.tree_position(
            "/somewhere/records",
            git=_fake_git(toplevel="/somewhere/records", counts="0\t13"))
        self.assertEqual(position.state, chain.TREE_BEHIND)
        self.assertEqual((position.ahead, position.behind), (0, 13))
        self.assertEqual(position.upstream_ref, "origin/main")
        # The fallback is reported AS a fallback: a branch that merely has a
        # same-named remote ref is not a branch that follows one.
        self.assertIn("branch.main.merge", position.why)
        self.assertIn("not one this branch follows", position.why)

    def test_no_upstream_and_no_ref_to_fall_back_to_is_unread(self):
        position = chain.tree_position(
            "/somewhere/records",
            git=_fake_git(toplevel="/somewhere/records", ref_exists=False))
        self.assertEqual(position.state, chain.TREE_UNREAD)
        self.assertIsNone(position.upstream_ref)
        self.assertIn("states what this branch is supposed to follow",
                      position.why)

    def test_a_path_that_is_not_a_checkout_is_unread_and_holds_nothing(self):
        position = chain.tree_position(
            "/somewhere/plain",
            git=lambda path, *a: (128, "fatal: not a git repository\n"))
        self.assertEqual(position.state, chain.TREE_UNREAD)
        self.assertIn("not a git checkout", position.why)
        # Not False. Nothing was established about this path, and reporting an
        # unanswerable question as an absent document is the defect itself.
        self.assertIsNone(position.holds("projects/p/items/I/brief.md"))

    def test_holds_reads_128_as_absent_and_anything_else_as_unanswerable(self):
        position = chain.tree_position(
            "/somewhere/records",
            git=_fake_git(toplevel="/somewhere/records", counts="0\t13",
                          has_files=("projects/p/items/I/brief.md",)))
        self.assertIs(True, position.holds("projects/p/items/I/brief.md"))
        self.assertIs(False, position.holds("projects/p/items/I/design"))
        self.assertIsNone(position.holds(None))
        other = chain.tree_position(
            "/somewhere/records",
            git=lambda path, *a: (0, "/somewhere/records\n") if a == (
                "rev-parse", "--show-toplevel") else (128, "fatal: bad object\n"))
        self.assertIsNone(other.holds("projects/p/items/I/brief.md"))

    def test_it_never_fetches(self):
        """A read verb's answer must not depend on the network.

        Every call the helper makes is either a `rev-parse`, a `rev-list` or a
        `cat-file -e` against refs already on disk. If a `fetch` ever appears
        here, `work item` starts answering differently depending on whether the
        network was up — which is how a read verb becomes a flaky one.
        """
        seen = []
        inner = _fake_git(toplevel="/somewhere/records", counts="0\t13",
                          has_files=("projects/p/items/I/brief.md",))

        def git(path, *args):
            seen.append(args)
            return inner(path, *args)

        position = chain.tree_position("/somewhere/records", git=git)
        self.assertIs(True, position.holds("projects/p/items/I/brief.md"))
        self.assertTrue(seen, "the helper asked its git nothing at all")
        for args in seen:
            self.assertNotIn("fetch", args)
            self.assertNotIn("pull", args)
            self.assertNotIn("ls-remote", args)


class AnAbsenceTheTreeExplainsIsNotAnAbsenceTheItemOwes(unittest.TestCase):
    """The distinction has to survive out to the payload, not just the sentence."""

    RULE = {"name": "brief.md", "resolved_by": ("exact-file",),
            "required_at": {"ACTIVE"}}

    def _documents(self, tmp, *, held, spellings=None, state="ACTIVE"):
        rule = dict(self.RULE)
        if spellings is not None:
            rule["resolved_by"] = tuple(spellings)
        position = chain.TreePosition(
            root=tmp, revision="a" * 40, branch="main", upstream_ref="origin/main",
            ahead=0, behind=13, state=chain.TREE_BEHIND,
            git=_fake_git(toplevel=str(tmp), counts="0\t13", has_files=held))
        return chain._documents(Path(tmp) / "projects/p/items/I", state, [rule],
                                None, position)[0]

    def test_the_authority_holding_it_rewrites_the_cause_and_not_the_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            answer = self._documents(tmp, held=(
                "projects/p/items/I/brief.md",))
        self.assertEqual(answer["state"], "MISSING",
                         "materializing nothing must not make the document readable")
        self.assertIs(True, answer["held_by_upstream"])
        self.assertEqual(answer["upstream_ref"], "origin/main")
        self.assertEqual(answer["upstream_path"], "brief.md")
        self.assertIn("not missing from the Item", answer["why"])
        self.assertIn("13 behind", answer["why"])
        self.assertNotIn("resolves under none of", answer["why"])

    def test_the_negative_control_leaves_the_sentence_alone(self):
        """The ref does not have it either, so the Item is the right party."""
        with tempfile.TemporaryDirectory() as tmp:
            answer = self._documents(tmp, held=())
        self.assertEqual(answer["state"], "MISSING")
        self.assertIs(False, answer["held_by_upstream"])
        self.assertIn("resolves under none of", answer["why"])
        self.assertNotIn("not missing from the Item", answer["why"])

    def test_an_unanswerable_question_claims_no_cause(self):
        with tempfile.TemporaryDirectory() as tmp:
            position = chain.TreePosition(
                root=tmp, revision=None, branch=None, upstream_ref=None,
                state=chain.TREE_UNREAD, why="nothing follows anything here",
                git=None)
            answer = chain._documents(
                Path(tmp) / "projects/p/items/I", "ACTIVE", [dict(self.RULE)],
                None, position)[0]
        self.assertEqual(answer["state"], "MISSING")
        self.assertIsNone(answer["held_by_upstream"])
        self.assertIn("resolves under none of", answer["why"])

    def test_no_tree_at_all_leaves_every_existing_caller_unchanged(self):
        """The whole reason `tree` is optional and last."""
        with tempfile.TemporaryDirectory() as tmp:
            answer = chain._documents(
                Path(tmp) / "projects/p/items/I", "ACTIVE", [dict(self.RULE)],
                None)[0]
        self.assertEqual(answer["state"], "MISSING")
        self.assertNotIn("held_by_upstream", answer)
        self.assertNotIn("upstream_ref", answer)

    def test_the_ref_is_asked_with_the_spellings_the_rule_would_accept(self):
        """`design/` is a DIRECTORY on this node; `design` is not the question.

        Asking the ref about `design` for a `suffixed-file` rule would report a
        document absent at the ref because the wrong name was looked for —
        this module's defect, one level down.
        """
        with tempfile.TemporaryDirectory() as tmp:
            answer = self._documents(
                tmp, held=("projects/p/items/I/brief.md",),
                spellings=("exact-file", "suffixed-file"))
        # `brief.md` resolves under `exact-file`; the suffixed spelling is
        # `brief.md.md` and is not at the ref. One True wins, and the line still
        # names the spelling that WAS found.
        self.assertIs(True, answer["held_by_upstream"])
        self.assertEqual(answer["upstream_path"], "brief.md")


if __name__ == "__main__":
    unittest.main()
