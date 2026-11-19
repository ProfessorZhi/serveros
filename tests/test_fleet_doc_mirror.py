"""A-4 / R-4: Git-side pages published one way into Feishu docs."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "integrations" / "fleet-hub-watch",
              ROOT / "integrations" / "fleet-next-agent-monitor"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fleet_hub_watch.mirror import DocMirror, parse_spec  # noqa: E402

NOW = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)


class Fake:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, cwd):
        self.calls.append((argv, (Path(cwd) / argv[argv.index("--content") + 1][3:]).read_text()))
        out = {"ok": True, "data": {"document": {"document_id": "doxABC",
                                                 "url": "https://x.feishu.cn/docx/doxABC"}}}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(out), stderr="")


class TheMirror(unittest.TestCase):
    def test_spec(self):
        self.assertEqual(parse_spec("board:工作看板=/a/b.md"), ("board", "工作看板", Path("/a/b.md")))
        with self.assertRaises(ValueError):
            parse_spec("nothing")

    def test_created_once_then_overwritten_only_on_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "current.md"
            src.write_text("# 看板\n- REQ-001\n", encoding="utf-8")
            fake, clock = Fake(), [1000.0]
            mirror = DocMirror([f"board:工作看板={src}"], "fldTOKEN", Path(tmp) / "s.json",
                               run=fake, clock=lambda: clock[0])
            self.assertEqual(mirror.tick(NOW), {"board": "CREATED"})
            argv, body = fake.calls[0]
            self.assertIn("+create", argv)
            self.assertEqual(argv[argv.index("--parent-token") + 1], "fldTOKEN")
            self.assertIn("只读副本", body)
            self.assertIn("REQ-001", body)
            clock[0] += 3600
            self.assertIsNone(mirror.tick(NOW))                    # unchanged: nothing sent
            src.write_text("# 看板\n- REQ-002\n", encoding="utf-8")
            self.assertEqual(mirror.tick(NOW), {"board": "UPDATED"})
            argv, _ = fake.calls[-1]
            self.assertEqual(argv[argv.index("--doc") + 1], "https://x.feishu.cn/docx/doxABC")
            self.assertIn("overwrite", argv)
            src.write_text("# 看板\n- REQ-003\n", encoding="utf-8")
            self.assertIsNone(mirror.tick(NOW))                    # within MIN_GAP
            self.assertEqual(mirror.health()["board"]["state"], "PUBLISHED")

    def test_a_failure_is_counted_and_retried_later(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "m.md"
            src.write_text("x", encoding="utf-8")
            failing = lambda argv, cwd: subprocess.CompletedProcess(argv, 1, stdout="", stderr="no")
            clock = [0.0]
            mirror = DocMirror([f"map:地图={src}"], None, Path(tmp) / "s.json", run=failing,
                               clock=lambda: clock[0] + 10_000)
            self.assertEqual(mirror.tick(NOW), {"map": "FAILED"})
            self.assertEqual(mirror.health()["map"]["failures"], 1)


if __name__ == "__main__":
    unittest.main()


class TheHubUnitKeepsIt(unittest.TestCase):
    def test_defaults_when_the_pages_exist_and_the_units_own_flags_win(self):
        sys.path.insert(0, str(ROOT / "distribution"))
        import device_kit
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(device_kit.hub_mirror_args("", home), "")
            board = home / "state/serveros/requirement-board/current.md"
            board.parent.mkdir(parents=True)
            board.write_text("x")
            got = device_kit.hub_mirror_args("", home)
            self.assertIn(f"--mirror board:工作看板（自动生成）={board}", got)
            self.assertIn(f"--mirror-folder {device_kit.MIRROR_FOLDER}", got)
            own = "ExecStart=/py -m fleet_hub_watch --mirror x:y=/p --mirror-folder F"
            self.assertEqual(device_kit.hub_mirror_args(own, home), " --mirror x:y=/p --mirror-folder F")
