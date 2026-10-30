"""The hub's requirement board, read and proposed to through Fleet (design §4)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fleet_mcp import tools as fleet_tools  # noqa: E402
from fleet_mcp.config import load_config  # noqa: E402
from fleet_mcp.workboard import WorkBinding, WorkBoard  # noqa: E402
from test_fleet_mcp_surface import Harness  # noqa: E402
from workengine.model import WorkItem  # noqa: E402

class Refused(Exception):
    def __init__(self, answer):
        super().__init__(answer.get("detail"))
        self.code = answer["code"]


PROPOSAL = dict(project="serveros", title="Run report misses the A/B SHA",
                kind="bug", source_kind="feishu", source="feishu:msg/om_123",
                reason="Reviewers cannot tell which code each container ran.")


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.harness = Harness(self.tmp)
        self.service = self.harness.service
        self.records = self.tmp / "records"
        self.board = WorkBoard(WorkBinding(state_dir=self.tmp / "state",
                                           records_root=self.records))
        self.service.work = self.board

    def tearDown(self):
        self._td.cleanup()

    def call(self, name, principal=None, **args):
        """The tool path a client takes; a refusal comes back as one."""
        principal = principal or self.harness.principal("fleet:read", "fleet:send")
        ctx = fleet_tools.ToolContext(service=self.service, principal=principal)
        answer = fleet_tools.call_tool(ctx, name, args)
        if answer["isError"]:
            raise Refused(answer["structuredContent"])
        return answer["structuredContent"]


class TheToolsAreServedAsDesigned(unittest.TestCase):
    def test_two_reads_and_one_proposal(self):
        by = fleet_tools.SERVED_BY_NAME
        self.assertTrue(by["fleet_work_board"].annotations["readOnlyHint"])
        self.assertTrue(by["fleet_project_understanding"].annotations["readOnlyHint"])
        add = by["fleet_work_candidate_add"].annotations
        self.assertFalse(add["readOnlyHint"])
        self.assertFalse(add["destructiveHint"])

    def test_the_proposal_requires_a_source_and_a_reason(self):
        schema = fleet_tools.SERVED_BY_NAME["fleet_work_candidate_add"].input_schema
        for field in ("source_kind", "source", "reason"):
            self.assertIn(field, schema["required"])

    def test_no_tool_accepts_numbers_or_closes_work(self):
        names = set(fleet_tools.tool_names())
        for verb in ("fleet_work_accept", "fleet_work_item_create", "fleet_work_close",
                     "fleet_work_prioritise"):
            self.assertNotIn(verb, names)


class NoBoardIsSaid(unittest.TestCase):
    def test_unbound_answers_not_configured(self):
        with tempfile.TemporaryDirectory() as td:
            harness = Harness(Path(td))
            answer = harness.service.work_board(harness.principal("fleet:read"), "serveros")
            self.assertEqual(answer["board"], "NOT_CONFIGURED")
            self.assertNotIn("columns", answer)


class Proposals(Base):
    def test_a_proposal_lands_as_a_candidate_with_its_source(self):
        added = self.call("fleet_work_candidate_add", **PROPOSAL)
        self.assertTrue(added["created"])
        self.assertEqual(added["candidate"]["state"], "NEW")
        board = self.call("fleet_work_board", project="serveros")
        self.assertEqual(board["counts"]["candidates"], 1)
        row = board["columns"]["candidates"][0]
        self.assertEqual(row["title"], PROPOSAL["title"])
        self.assertEqual(row["sources"], ["feishu:msg/om_123"])

    def test_the_same_requirement_from_the_same_source_is_one_candidate(self):
        first = self.call("fleet_work_candidate_add", **PROPOSAL)
        again = self.call("fleet_work_candidate_add",
                          **(PROPOSAL | {"title": "  run REPORT misses the a/b sha "}))
        self.assertFalse(again["created"])
        self.assertEqual(again["candidate"]["candidate_id"],
                         first["candidate"]["candidate_id"])
        self.assertEqual(self.call("fleet_work_board", project="serveros")["counts"]["candidates"], 1)

    def test_a_different_source_is_a_different_candidate(self):
        self.call("fleet_work_candidate_add", **PROPOSAL)
        other = self.call("fleet_work_candidate_add",
                          **(PROPOSAL | {"source_kind": "online-error", "source": "log:proxy/42"}))
        self.assertTrue(other["created"])

    def test_proposing_needs_fleet_send(self):
        with self.assertRaises(Refused) as caught:
            self.call("fleet_work_candidate_add",
                      principal=self.harness.principal("fleet:read"), **PROPOSAL)
        self.assertEqual(caught.exception.code, "SCOPE_DENIED")

    def test_reading_needs_fleet_read(self):
        with self.assertRaises(Refused) as caught:
            self.call("fleet_work_board", principal=self.harness.principal("fleet:send"),
                      project="serveros")
        self.assertEqual(caught.exception.code, "SCOPE_DENIED")

    def test_bad_fields_are_refused_by_name(self):
        for bad, word in (({"kind": "wish"}, "kind"), ({"source_kind": "rumour"}, "source_kind"),
                          ({"reason": " "}, "reason"), ({"project": "../etc"}, "project")):
            with self.subTest(bad=bad):
                with self.assertRaises(Refused) as caught:
                    self.call("fleet_work_candidate_add", **(PROPOSAL | bad))
                self.assertEqual(caught.exception.code, "BAD_REQUEST")
                self.assertIn(word, str(caught.exception))

    def test_other_projects_are_not_on_this_board(self):
        self.call("fleet_work_candidate_add", **(PROPOSAL | {"project": "litellm-bridge"}))
        self.assertEqual(self.call("fleet_work_board", project="serveros")["counts"]["candidates"], 0)


class ItemsByState(Base):
    def test_items_are_grouped_into_columns(self):
        store = self.board.store
        store.register_project("serveros", "SRV")
        for state in ("READY", "ACTIVE", "DELIVERED", "CLOSED"):
            item_id = store.allocate_item_id("serveros")
            store.create_item(WorkItem(id=item_id, project="serveros", title=f"t-{state}",
                                       kind="feature", card_locator=f"cards/{item_id}.md"))
            path = {"READY": ["AVAILABLE", "READY"], "ACTIVE": ["AVAILABLE", "READY", "ACTIVE"],
                    "DELIVERED": ["AVAILABLE", "READY", "ACTIVE", "DELIVERED"],
                    "CLOSED": ["AVAILABLE", "READY", "ACTIVE", "DELIVERED", "CLOSED"]}[state]
            for target in path:
                try:
                    store.transition_item(item_id, target)
                except Exception:  # noqa: BLE001 - a path the state machine shortens
                    pass
        board = self.call("fleet_work_board", project="serveros")
        columns = {name: [r["state"] for r in rows]
                   for name, rows in board["columns"].items() if name != "candidates"}
        for name, rows in columns.items():
            for state in rows:
                from fleet_mcp.workboard import COLUMNS
                self.assertEqual(COLUMNS[state], name)
        self.assertEqual(sum(board["counts"][c] for c in ("todo", "doing", "review", "done")), 4)


class Understanding(Base):
    def test_not_recorded_until_something_is_written(self):
        answer = self.call("fleet_project_understanding", project="serveros")
        self.assertEqual(answer["understanding"], "NOT_RECORDED")

    def test_records_are_returned_as_written(self):
        directory = self.records / "projects" / "serveros"
        directory.mkdir(parents=True)
        (directory / "UNDERSTANDING.md").write_text("# serveros\n目标：设备侧能力。\n",
                                                    encoding="utf-8")
        answer = self.call("fleet_project_understanding", project="serveros")
        self.assertEqual(answer["understanding"], "OK")
        self.assertEqual(answer["records"][0]["name"], "UNDERSTANDING.md")
        self.assertIn("设备侧能力", answer["records"][0]["content"])

    def test_a_symlinked_record_is_not_followed(self):
        directory = self.records / "projects" / "serveros"
        directory.mkdir(parents=True)
        secret = self.tmp / "secret.md"
        secret.write_text("token", encoding="utf-8")
        (directory / "LEAK.md").symlink_to(secret)
        answer = self.call("fleet_project_understanding", project="serveros")
        self.assertEqual(answer["records"], [])


class TheBindingIsRead(unittest.TestCase):
    def test_work_state_dir_and_records_root_come_from_the_binding(self):
        with tempfile.TemporaryDirectory() as td:
            binding = Path(td) / "fleet.yaml"
            binding.write_text(
                "schema: serveros/fleet-mcp.local/v1\n"
                "device_ref: device:tokyo-shared-dev-01\n"
                f"work:\n  state_dir: {td}/state\n  records_root: {td}/records\n",
                encoding="utf-8")
            config = load_config(binding)
            self.assertEqual(config.work.state_dir, Path(td) / "state")
            self.assertEqual(config.work.records_root, Path(td) / "records")


if __name__ == "__main__":
    unittest.main()
