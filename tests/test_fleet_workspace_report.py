"""R-1: the daily directory check reaches the journal, and a weekly card sums it up."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "integrations" / "fleet-hub-watch",
              ROOT / "integrations" / "fleet-next-agent-monitor"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fleet_hub_watch import workspace  # noqa: E402
from fleet_mcp import device_versions  # noqa: E402


CHECK = {"schema": "serveros/workspace-check.v1", "device": "tokyo", "at": "2026-11-18T23:40:00Z",
         "plan_id": "abc123", "counts": {"MOVE_WORKTREE": 20, "REMOVE_WORKTREE": 150, "MOVE_REPO": 3,
                                         "DEFER": 2},
         "unregistered": ["/data/home/vince/odd-dir"], "worktrees_outside_work": 170,
         "in_use_deferred": ["/data/home/vince/busy"]}


def versions(check: dict | None, state: str = "RECORDED") -> dict:
    device = {"device_ref": "device:tokyo", "display_name": "东京", "state": state}
    if check is not None:
        device["workspace"] = check
    return {"devices": [device]}


class TheDeviceRecord(unittest.TestCase):
    def test_ws_records_where_the_connector_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "HOME": tmp, "LOCALAPPDATA": tmp}
            code = ("import importlib.util,sys;from pathlib import Path;"
                    f"s=importlib.util.spec_from_file_location('ws','{ROOT}/distribution/workspace/ws.py');"
                    "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
                    f"sys.path.insert(0,'{ROOT}');from fleet_mcp import device_versions as d;"
                    "print(m.record_path()==d.workspace_path())")
            out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "True", out.stderr)

    def test_the_record_is_projected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workspace-check.json"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
            with os.fdopen(fd, "w") as handle:
                json.dump({**CHECK, "unregistered": [f"/x/{i}" for i in range(50)]}, handle)
            got = device_versions.workspace_check(path)
            self.assertEqual(got["state"], "RECORDED")
            self.assertEqual(got["unregistered"], 50)
            self.assertEqual(len(got["unregistered_names"]), 20)
            self.assertEqual(got["in_use_deferred"], 1)
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(device_versions.workspace_check(path)["state"], "UNREADABLE")
            self.assertEqual(device_versions.workspace_check(Path(tmp) / "none.json")["state"],
                             "NOT_RECORDED")


class TheHubKeepsIt(unittest.TestCase):
    def _report(self, tmp: Path, answer: dict, sent: list):
        return workspace.WorkspaceReport(
            tmp, lambda: answer,
            send=lambda key, card, now: sent.append((key, card)) or True,
            card=lambda title, lines, now, template: {"title": title, "lines": lines,
                                                      "template": template})

    def test_once_a_day_after_nine_china_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            sent: list = []
            report = self._report(Path(tmp), versions(device_versions_like()), sent)
            early = datetime(2026, 11, 18, 0, 30, tzinfo=timezone.utc)      # 08:30 CN
            self.assertIsNone(report.tick(early))
            later = datetime(2026, 11, 18, 2, 0, tzinfo=timezone.utc)       # 10:00 CN, Wed
            done = report.tick(later)
            self.assertEqual(done, {"daily": "workspace/2026/11/18.md"})
            self.assertIsNone(report.tick(later + timedelta(hours=1)))
            text = (Path(tmp) / done["daily"]).read_text(encoding="utf-8")
            self.assertIn("待搬 23", text)
            self.assertIn("可回收 150", text)
            self.assertIn("odd-dir", text)
            self.assertEqual(sent, [])

    def test_a_weekly_card_on_monday_with_the_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            sent: list = []
            week_ago = datetime(2026, 11, 16, 2, 0, tzinfo=timezone.utc)   # Monday
            self._report(Path(tmp), versions(device_versions_like()), sent).tick(week_ago)
            fewer = device_versions_like(REMOVE_WORKTREE=40)
            report = self._report(Path(tmp), versions(fewer), sent)
            done = report.tick(week_ago + timedelta(days=7))
            self.assertEqual(done["weekly"], "SENT")
            key, card = sent[-1]
            self.assertEqual(key, "workspace-week:2026-W48")
            self.assertIn("可回收 40（-110）", card["lines"][0])
            self.assertIsNone(report.tick(week_ago + timedelta(days=7, hours=2)))

    def test_a_device_without_the_new_connector_is_said_plainly(self):
        rows = workspace.rows_from(versions(None))
        self.assertEqual(rows[0]["state"], "NOT_REPORTED")
        text = workspace.page(rows, datetime(2026, 11, 18, 2, tzinfo=timezone.utc))
        self.assertIn("需要更新", text)


def device_versions_like(**counts) -> dict:
    return {"state": "RECORDED", "at": "2026-11-18T23:40:00Z", "plan_id": "abc123",
            "counts": {**CHECK["counts"], **counts}, "unregistered": 1,
            "unregistered_names": ["/data/home/vince/odd-dir"], "worktrees_outside_work": 170,
            "in_use_deferred": 1}


if __name__ == "__main__":
    unittest.main()
