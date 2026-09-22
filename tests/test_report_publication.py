"""B8 — the compatibility facade, and the four things it is still carrying.

设计 §8 把 B8 写成一句有顺序的话：保留兼容入口、移除四处职责、只在确有必要时新建 Skill
身份，而且**先保留入口再迁 consumer**。做完第一件的交付和做完前两件的交付，读起来是一样
的——除非有一份东西把「还欠什么、欠给谁、被谁挡住」写成可被拒绝的字段。

这个文件测的就是那份东西被拒绝的那几次。一份只会通过的契约检查等于没有检查：真正要钉住的
是「少写一处」「把等待写成已迁」「把行号写成不带 revision 的坐标」这几种**读起来仍然成立**
的写法。
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


publication = _load("serveros_runtime_report_publication",
                    ROOT / "runtime" / "report_publication.py")
yaml = publication.yaml


class Fixture(unittest.TestCase):
    """A contract that is perturbed one field at a time.

    Every case starts from the real file, so a test cannot pass because a
    fixture drifted into a shape the real contract no longer has.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.document = yaml.safe_load(publication.CONTRACT.read_text(encoding="utf-8"))

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, document=None) -> Path:
        path = self.dir / "contract.yaml"
        path.write_text(yaml.safe_dump(document if document is not None
                                       else self.document, allow_unicode=True),
                        encoding="utf-8")
        return path

    def refuse(self, mutate, *expected: str) -> str:
        document = copy.deepcopy(self.document)
        mutate(document)
        with self.assertRaises(publication.PublicationError) as caught:
            publication.contract(self.write(document))
        message = str(caught.exception)
        for fragment in expected:
            self.assertIn(fragment, message)
        return message


# ---------------------------------------------------------------------------
# the real contract
# ---------------------------------------------------------------------------


class TheContractAsShipped(unittest.TestCase):
    def test_it_validates(self):
        document = publication.contract()
        self.assertEqual(document["schema"], publication.CONTRACT_SCHEMA)

    def test_the_removals_are_exactly_the_four_the_design_names(self):
        self.assertEqual(tuple(entry["id"] for entry in publication.delegations()),
                         publication.DESIGN_DELEGATIONS)

    def test_the_compatibility_entry_is_still_the_only_one(self):
        """设计 §8 的第三件事：没有新建 `reporting`，而这里要能读出它没被新建。"""
        facade = publication.contract()["facade"]
        self.assertEqual(facade["ability"], "feishu-report")
        self.assertIs(facade["new_skill_identity"], False)

    def test_every_evidence_line_carries_a_full_revision(self):
        for entry in publication.delegations():
            self.assertRegex(str(entry["at_revision"]), r"^[0-9a-f]{40}$",
                             f"{entry['id']} 的行号没有绑定 revision，"
                             f"而本仓已经因为读工作树行号得到过假结论")

    def test_the_one_consumer_is_waiting_and_says_what_it_holds(self):
        waiting = publication.waiting()
        self.assertEqual(len(waiting), 1)
        entry = waiting[0]
        self.assertEqual(entry["id"], "srv-0005-e2e-evidence")
        self.assertEqual(entry["disposition"],
                         "MIGRATION_WAITING_ON_ACTIVE_CONSUMER")
        self.assertTrue(entry["paths"])
        self.assertTrue(entry["pending_interfaces"])

    def test_the_three_owners_are_named_and_the_removal_is_not(self):
        owners = {entry["id"]: entry["owners"] for entry in publication.delegations()}
        self.assertEqual(owners["space-rules"], ["feishu-space-management"])
        self.assertEqual(owners["default-whole-overwrite"], ["document-stewardship"])
        self.assertIn("test-management", owners["copied-test-execution"])
        self.assertEqual(owners["compulsory-milestone"], [],
                         "设计说的是取消这条要求，不是把它挪给别人；"
                         "给它编一个接手方会把一次取消读成一次委派")


# ---------------------------------------------------------------------------
# the refusals
# ---------------------------------------------------------------------------


class AContractThatSaysLessThanItKnows(Fixture):
    def test_a_dropped_removal_is_refused(self):
        message = self.refuse(lambda d: d["delegations"].pop(),
                              "compulsory-milestone", "少")
        self.assertIn("compulsory-milestone", message)

    def test_an_invented_fifth_removal_is_refused(self):
        def add(document):
            document["delegations"].append(copy.deepcopy(document["delegations"][0]))
            document["delegations"][-1]["id"] = "second-overwrite"
        self.refuse(add, "second-overwrite", "多")

    def test_an_empty_removal_list_is_refused(self):
        self.refuse(lambda d: d.__setitem__("delegations", []), "欠着四处")

    def test_an_ownerless_removal_with_no_reason_is_refused(self):
        def strip(document):
            document["delegations"][0]["owners"] = []
            document["delegations"][0].pop("why")
        self.refuse(strip, "长得完全一样")

    def test_a_removal_marked_migrated_is_refused(self):
        def mark(document):
            document["delegations"][0]["state"] = "MIGRATED"
        self.refuse(mark, "MIGRATED", "消失")

    def test_evidence_without_a_revision_is_refused(self):
        def strip(document):
            document["delegations"][0].pop("at_revision")
        self.refuse(strip, "行号只在指定 revision 时才有意义")

    def test_a_short_revision_is_not_a_revision(self):
        def shorten(document):
            document["delegations"][0]["at_revision"] = "074a518"
        self.refuse(shorten, "完整 sha")

    def test_evidence_that_names_no_line_is_refused(self):
        def strip(document):
            document["delegations"][0]["evidence"][0].pop("line")
        self.refuse(strip, "line")

    def test_no_evidence_at_all_is_refused(self):
        def strip(document):
            document["delegations"][0]["evidence"] = []
        self.refuse(strip, "必须有坐标")

    def test_a_consumer_with_no_pending_interfaces_is_refused(self):
        def strip(document):
            document["consumers"][0]["pending_interfaces"] = []
        self.refuse(strip, "要按哪张表逐条落")

    def test_a_consumer_nobody_is_waiting_on_is_refused(self):
        def strip(document):
            document["consumers"][0]["disposition"] = "MIGRATED"
        self.refuse(strip, "MIGRATED")

    def test_no_consumer_at_all_is_refused(self):
        def strip(document):
            document["consumers"] = []
        self.refuse(strip, "一次没有对象的等待无法被复核")


class AContractThatNamesTheWrongRoute(Fixture):
    def test_a_route_string_that_points_elsewhere_is_refused(self):
        def move(document):
            document["facade"]["route_authorities"][0]["file"] = self._moved()
        message = self.refuse(move, "两处定义必须一致")
        self.assertIn("feishu-report", message)

    def test_a_capability_list_that_does_not_name_the_facade_is_refused(self):
        def move(document):
            document["facade"]["route_authorities"][1]["section"] = "KIND_CAPABILITIES.test"
        self.refuse(move, "两处定义必须一致")

    def test_an_authority_section_that_does_not_exist_is_refused(self):
        def move(document):
            document["facade"]["route_authorities"][1]["section"] = "KIND_CAPABILITIES.nope"
        self.refuse(move, "永远红，或者永远绿")

    def test_an_authority_of_an_unknown_form_is_refused(self):
        def move(document):
            document["facade"]["route_authorities"][0]["form"] = "vibes"
        self.refuse(move, "route-string")

    def test_an_authority_file_that_is_absent_is_refused(self):
        def move(document):
            document["facade"]["route_authorities"][0]["file"] = "skills/serveros/nope.json"
        self.refuse(move, "读不出来")

    def _moved(self) -> str:
        """A route table in which `report.publish` points at something else."""
        path = self.dir / "moved.json"
        path.write_text(json.dumps({
            "schema": "serveros/ability-contract.v1",
            "routes": {"report.publish": "some-other-skill"},
        }), encoding="utf-8")
        return str(path)


class AContractThatGrowsASecondSkill(Fixture):
    def test_claiming_a_new_skill_identity_is_refused(self):
        def claim(document):
            document["facade"]["new_skill_identity"] = True
        self.refuse(claim, "只有实际需要才独立新 Skill 身份")

    def test_an_absent_facade_is_refused(self):
        def drop(document):
            document.pop("facade")
        self.refuse(drop, "这份契约的主语")

    def test_a_compose_that_points_nowhere_is_refused(self):
        def move(document):
            document["compose"]["module"] = "runtime/no_such_producer.py"
        self.refuse(move, "指不到 producer")

    def test_a_compose_with_no_module_is_refused(self):
        def strip(document):
            document["compose"].pop("module")
        self.refuse(strip, "还没有实现")

    def test_an_unknown_schema_is_refused(self):
        def relabel(document):
            document["schema"] = "serveros/report-publication-contract.v2"
        self.refuse(relabel, "空契约里每一条约束都为真")


class ThisIsADeclarationAndNotATransport(unittest.TestCase):
    def test_it_imports_no_transport_and_no_subprocess(self):
        source = (ROOT / "runtime" / "report_publication.py").read_text(encoding="utf-8")
        for banned in ("import subprocess", "import socket", "urllib",
                       "requests", "docker", "lark"):
            self.assertNotIn(banned, source,
                             f"一份能碰到投递的兼容层，早晚会替 B9 和 B10 做决定：{banned}")

    def test_the_diagnostic_reads_the_shipped_contract(self):
        import io
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(publication.main([]), 0)
        self.assertIn("feishu-report", buffer.getvalue())

    def test_the_diagnostic_refuses_a_contract_it_cannot_read(self):
        import io
        from contextlib import redirect_stderr
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            self.assertEqual(publication.main(["--contract", "/nonexistent.yaml"]), 1)
        self.assertIn("REFUSED", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
