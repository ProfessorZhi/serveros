"""The interposer must pass bytes unchanged and log metadata only."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTERPOSER = ROOT / "integrations" / "fleet-cc-dev-plugin" / "stdio_interposer.py"


class TheInterposerIsTransparent(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="interposer-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.log = self.tmp / "wire.jsonl"
        self.child = self.tmp / "child.py"
        self.child.write_text(
            "import json,sys\n"
            "for line in sys.stdin:\n"
            "    event=json.loads(line)\n"
            "    if event.get('method')=='initialize':\n"
            "        out={'jsonrpc':'2.0','id':event.get('id'),'result':{'serverInfo':{'name':'x','version':'1'},'secret':'DO_NOT_LOG'}}\n"
            "    elif event.get('method')=='tools/list':\n"
            "        out={'jsonrpc':'2.0','id':event.get('id'),'result':{'tools':[{'name':'a'},{'name':'b'}]}}\n"
            "    else:\n"
            "        out={'jsonrpc':'2.0','id':event.get('id'),'result':{}}\n"
            "    sys.stdout.write(json.dumps(out)+'\\n'); sys.stdout.flush()\n",
            encoding="utf-8")

    def test_bytes_are_passed_and_log_has_only_metadata(self) -> None:
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        payload = "".join(json.dumps(row) + "\n" for row in requests).encode()
        proc = subprocess.run(
            [sys.executable, str(INTERPOSER), "--log", str(self.log), "--",
             sys.executable, str(self.child)],
            input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        lines = [json.loads(line) for line in proc.stdout.decode().splitlines()]
        self.assertEqual([row["id"] for row in lines], [1, 2])
        self.assertEqual(lines[1]["result"]["tools"][0]["name"], "a")
        log = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertTrue(any(row.get("method") == "initialize" for row in log))
        self.assertTrue(any(row.get("tool_count") == 2 for row in log))
        self.assertNotIn("DO_NOT_LOG", self.log.read_text())

    def test_a_missing_child_is_not_replaced_with_a_synthetic_answer(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(INTERPOSER), "--log", str(self.log), "--",
             str(self.tmp / "does-not-exist")],
            input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")


if __name__ == "__main__":
    unittest.main()
