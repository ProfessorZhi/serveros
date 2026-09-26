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
    def line(card: dict) -> str:
        found = [e["content"] for e in card["body"]["elements"]
                 if isinstance(e.get("content"), str)
                 and e["content"].startswith("**播报日程**")]
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

    def test_an_absent_calendar_becomes_a_named_gap_on_the_card(self):
        card = self.card("--at", AT, "--business-calendar",
                         str(self.dir / "nothing-here.yaml"))
        text = " ".join(e.get("content", "") for e in card["body"]["elements"]
                        if isinstance(e.get("content"), str))
        self.assertIn("采集缺口", text)
        self.assertIn("business_calendar", text)


if __name__ == "__main__":
    unittest.main()
