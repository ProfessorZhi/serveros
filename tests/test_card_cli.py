#!/usr/bin/env python3
"""`serverosctl card status`, driven through its own argument parser.

WHY THIS FILE EXISTS
--------------------
The card's parts were each tested and the COMMAND was not: `tests/test_cards.py`
proves the renderer against a model, `tests/test_card_delivery.py` proves the
receipt, and nothing proved that `serverosctl card status` assembles them — so an
argument the parser never declares, a keyword the caller forgets to pass, or a
block the command drops between gathering and rendering would all have been
invisible until somebody ran it on the node. It was: the schedule line's
`--business-calendar` path was exercised by hand before this file existed, and a
hand run is evidence about one afternoon rather than about the command.

WHAT IS STUBBED, AND WHAT THAT COSTS
------------------------------------
`card_source.gather` only. It runs four `serverosctl` subprocesses, so leaving it
real would make this file measure the host — and a test that fails when the
machine is busy is a test somebody eventually stops running. The seam is the
module object the CLI itself resolves (`_load_module_from` returns whatever is in
`sys.modules` under that name), so what is replaced is exactly what the command
would have called, not a copy of it.

Everything past the gather is the real thing: the real parser, the real
snapshot, the real model, the real renderer.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
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


#: The same name `runtime/cli.py::_card_modules` resolves, which is why patching
#: this object patches what the command calls.
source = _load("serveros_runtime_card_source", ROOT / "runtime" / "card_source.py")
cli = _load("serveros_runtime_cli_for_cards", ROOT / "runtime" / "cli.py")

AT = "2031-06-10T09:30:00+08:00"

#: One gather, small enough to read. Every value here is a fact about nobody.
COLLECTED = {
    "reads": {
        "summary": {
            "host": {"cpu": {"logical_cpus": 8, "model": "stub"},
                     "load": {"load1": 0.1, "load5": 0.1, "load15": 0.1,
                              "runnable": 1},
                     "memory": {"used_bytes": 1024, "total_bytes": 2048,
                                "available_bytes": 1024, "cached_bytes": 0,
                                "swap": {"used_bytes": 0, "total_bytes": 0,
                                         "used_percent": 0.0}}},
            "user": {"summary": {"process_count": 1,
                                 "cpu_percent_of_host": 0.0, "cpu_cores": 0.0,
                                 "logical_cpus": 8, "memory_rss_bytes": 1024,
                                 "memory_percent_of_host": 0.0,
                                 "provenance": "stub"},
                     "quota": {}},
            "disk": {"roles": []},
            "docker": {},
            "advisory": {"thresholds": {}, "notes": []},
        },
        "user_services": {"units": [], "linger": "yes",
                          "supervisor": "systemd --user"},
        "runtime_list": {"resources": []},
    },
    "gaps": [],
    "read_names": ["summary", "top", "user_services", "runtime_list"],
}

#: A synthetic year. `tests/test_business_calendar.py` says at length why no
#: official calendar is copied into this tree; the Saturday-as-workday here is
#: what makes this one unmistakable for an import.
CALENDAR = """\
schema: serveros/business-calendar.v1
jurisdiction: CN-Mainland
timezone: Asia/Shanghai
source:
  document_id: synthetic-test-notice-2031
  revision: synthetic-rev-1
years:
  "2031":
    workdays: ["2031-06-07"]
    non_working_days: ["2031-06-09"]
imported_at: "2030-12-01T00:00:00+08:00"
verified_at: "2030-12-01T00:00:00+08:00"
"""


class CardStatusTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="card-cli-test-"))
        self.calendar = self.dir / "business-calendar.yaml"
        self.calendar.write_text(CALENDAR, encoding="utf-8")
        real_gather = source.gather
        source.gather = lambda *a, **k: json.loads(json.dumps(COLLECTED))
        self.addCleanup(setattr, source, "gather", real_gather)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def card(self, *argv) -> dict:
        """The rendered card, through the command's own parser and exit path."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(["card", "status", "--json", *argv])
        self.assertEqual(code, 0, out.getvalue())
        return json.loads(out.getvalue())

    @staticmethod
    def _strings(node) -> list[str]:
        """Every string the card carries, at any depth.

        Depth because the schedule sentence moved: it was a top-level element
        and §1's screenshot lists a workday rule among the lines a reader has to
        get past before reaching a reading, so §M4 puts it in 详情. The
        assertions below are about what the card SAYS and survive the move;
        `test_the_schedule_sentence_is_not_on_the_first_screen` holds the other
        half, which is where a reader meets it.
        """
        if isinstance(node, dict):
            found = ([node["content"]] if isinstance(node.get("content"), str)
                     else [])
            for value in node.values():
                found.extend(CardStatusTests._strings(value))
            return found
        if isinstance(node, list):
            found = []
            for item in node:
                found.extend(CardStatusTests._strings(item))
            return found
        return []

    @classmethod
    def line(cls, card: dict) -> str:
        """The schedule sentence, wherever the card keeps it.

        Line-wise as well as deep: §M4 moved the sentence into the 技术诊断
        fold, and that fold is ONE markdown element carrying several sentences
        separated by newlines. A search over whole elements therefore finds the
        fold and misses every sentence in it — which is how this helper came to
        report the schedule sentence as absent from a card that was printing it.
        """
        found = [line for text in cls._strings(card)
                 for line in text.splitlines() if line.startswith("播报日程 ")]
        assert len(found) == 1, f"the schedule line is not on the card: {found}"
        return found[0]

    def test_a_declared_workday_inside_the_window_says_so(self):
        card = self.card("--at", AT, "--business-calendar", str(self.calendar))
        line = self.line(card)
        self.assertIn("2031-06-10 是工作日", line)
        self.assertIn("08:00–20:00", line)
        self.assertIn("2/13", line)

    def test_a_declared_rest_day_claims_no_routine_broadcast(self):
        card = self.card("--at", "2031-06-09T12:00:00+08:00",
                         "--business-calendar", str(self.calendar))
        self.assertIn("不创建例行状态卡", self.line(card))

    def test_no_calendar_is_unknown_and_never_a_workday(self):
        """The command with no `--business-calendar` reads the instance path,
        which on a node that has not imported one is absent."""
        card = self.card("--at", AT, "--business-calendar",
                         str(self.dir / "nothing-here.yaml"))
        line = self.line(card)
        self.assertIn("calendar=UNKNOWN", line)
        self.assertNotIn("是工作日，现在是", line)

    def test_the_command_still_renders_when_the_calendar_argument_shifts(self):
        """Two calls in one day differ in the tick and agree on the day."""
        morning = self.card("--at", "2031-06-10T08:00:00+08:00",
                            "--business-calendar", str(self.calendar))
        evening = self.card("--at", "2031-06-10T17:00:00+08:00",
                            "--business-calendar", str(self.calendar))
        self.assertIn("1/13", self.line(morning))
        self.assertIn("10/13", self.line(evening))
        self.assertIn("2031-06-10", self.line(evening))

    def test_the_schedule_sentence_is_not_on_the_first_screen(self):
        """§1's screenshot and §M4: which workday this is and how the broadcast
        window was decided are accounts of the card, not readings from the
        machine, and they belong behind the fold that carries the rest of them.

        Both halves are asserted, because either alone passes on the wrong card:
        a card with no schedule sentence at all would satisfy "not on the face",
        and the old card satisfied "on the card" while putting it first.
        """
        card = self.card("--at", AT, "--business-calendar", str(self.calendar))
        on_face = [e["content"] for e in card["body"]["elements"]
                   if isinstance(e.get("content"), str)]
        self.assertFalse([text for text in on_face if "播报日程" in text], on_face)
        self.assertIn("2031-06-10 是工作日", self.line(card))

    def test_an_absent_calendar_becomes_a_named_gap_on_the_card(self):
        """Named to the reader on the face, and to the engineer in the fold.

        The gap is still NAMED — that has not changed and is the point of the
        test — but it is named twice, in the two vocabularies §2 and §R15
        separate. On the face the reading is 工作日历，named the way a person
        knows it; `business_calendar` is a schema key, and §2 forbids schema on
        a first screen. The key is not dropped: one tap down, 技术诊断 carries
        it beside the path and the reason, which is where an account of how the
        reading was taken belongs. Both halves are asserted, because either
        alone passes on the wrong card — a card that lost the key would satisfy
        "not on the face", and the old card satisfied "on the card" while
        putting the key exactly where the reading should be.
        """
        card = self.card("--at", AT, "--business-calendar",
                         str(self.dir / "nothing-here.yaml"))
        on_face = [e["content"] for e in card["body"]["elements"]
                   if isinstance(e.get("content"), str)]
        self.assertIn("**采集缺口** 工作日历 未读到", on_face)
        self.assertFalse([text for text in on_face if "business_calendar" in text],
                         "the schema key is back on the first screen")
        # `_strings`, not `line`: `line` is the schedule sentence's own reader
        # and asserts it found exactly one, so it is the wrong instrument here.
        in_fold = [line for text in self._strings(card)
                   for line in text.splitlines()
                   if line.startswith("· business_calendar")]
        self.assertEqual(len(in_fold), 1, self._strings(card))


if __name__ == "__main__":
    unittest.main()
