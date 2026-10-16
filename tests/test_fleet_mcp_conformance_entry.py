#!/usr/bin/env python3
"""The conformance entry keeps running, and keeps saying what it does NOT know.

`integrations/fleet-v1-mcp-conformance/run.py` is the entry a release candidate
runs before Next goes live, and it is the only thing in this repository that
drives `McpServer.handle` over real JSON-RPC. Two things about it need pinning
or it quietly stops being either:

  * it must pass, through the protocol layer, with no device, no socket and no
    credential — the whole reason it exists is that code acceptance must not be
    gated on a ChatGPT login;
  * its real-origin section must stay `NOT_RUN` unless asked for. A receipt that
    reads as "the real route was exercised" when no real route was touched is
    worse than no receipt, because it is quoted.

Nothing here starts a service or opens a port. The real-origin path is asserted
by its ABSENCE of a call: this suite passes no `--next-readonly` and requires
the section to say so.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
import contextlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "integrations" / "fleet-v1-mcp-conformance" / "run.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("fleet_mcp_conformance", ENTRY)
conformance = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(conformance)


def run(*argv: str) -> tuple[int, dict]:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = conformance.main(list(argv))
    return code, json.loads(stdout.getvalue())


class TheEntryPassesWithNoDeviceAndNoCredential(unittest.TestCase):
    def test_every_check_passes_and_the_exit_code_says_so(self):
        code, receipt = run()
        self.assertEqual(code, 0, [c for c in receipt["checks"]
                                   if c["outcome"] != "PASS"])
        self.assertEqual(receipt["counts"]["fail"], 0)
        self.assertGreaterEqual(receipt["counts"]["pass"], 19)
        self.assertEqual(receipt["mode"], "FAKE_DEVICE")

    def test_it_carries_the_surface_revision_rather_than_a_tool_count(self):
        _, receipt = run()
        self.assertEqual(receipt["schema"], conformance.SCHEMA)
        self.assertEqual(receipt["server"]["surface_revision"],
                         conformance.TOOL_SURFACE_REVISION)

    def test_every_check_names_a_class_and_an_outcome(self):
        _, receipt = run()
        for check in receipt["checks"]:
            self.assertLessEqual({"id", "class", "outcome", "detail"}, set(check))
            self.assertIn(check["outcome"], {"PASS", "FAIL", "NOT_RUN"})

    def test_the_run_covers_the_protocol_the_contract_and_the_gates(self):
        _, receipt = run()
        classes = {check["class"] for check in receipt["checks"]}
        for expected in ("protocol", "contract", "grant", "runtime"):
            self.assertIn(expected, classes)

    def test_a_real_origin_is_not_touched_unless_it_is_asked_for(self):
        _, receipt = run()
        real = receipt["real_next_readonly"]
        self.assertEqual(real["outcome"], "NOT_RUN")
        self.assertIn("says nothing about any real route", real["reason"])

    def test_the_receipt_says_which_mode_produced_it(self):
        _, receipt = run()
        self.assertIn("fake device", receipt["note"])
        self.assertIn("NOT_RUN", receipt["note"])


class TheReceiptIsWrittenWhereItIsAskedFor(unittest.TestCase):
    def test_a_receipt_path_gets_the_same_document(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipt.json"
            code, receipt = run("--receipt", str(path))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), receipt)


if __name__ == "__main__":
    unittest.main()
