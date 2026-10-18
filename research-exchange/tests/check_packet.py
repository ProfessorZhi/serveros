"""Mechanism tests for tools/check_packet.py.

All fixtures are synthetic text created in temporary directories. They are
structure/permission checks, not business evidence, and they do not assert any
real research or approval happened.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_packet  # noqa: E402

REV = "a" * 40
OTHER_REV = "b" * 40
NOTE_TEXT = "synthetic fixture text, not business evidence\n"


class PacketCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="check-packet-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "req-0001-demo"
        (self.base / "inputs").mkdir(parents=True)
        (self.base / "results" / "res-0001").mkdir(parents=True)
        (self.base / "inputs" / "note.txt").write_text(NOTE_TEXT, encoding="utf-8")
        (self.base / "results" / "res-0001" / "report.md").write_text(
            "# synthetic report\n", encoding="utf-8"
        )
        self.note_sha = hashlib.sha256(NOTE_TEXT.encode("utf-8")).hexdigest()

    # -- fixture builders -------------------------------------------------- #
    def packet(self, **overrides):
        doc = {
            "schema": "research-packet.v1",
            "request_id": "req-0001-demo",
            "project_ref": "serveros/litellm",
            "item_ref": "SRV-0011",
            "run_ref": "RUN-0010",
            "objective": "Check that the packet validator rejects empty required fields.",
            "questions": ["Does the validator reject an empty packet template?"],
            "non_goals": ["No production access"],
            "inputs": [
                {"path": "inputs/note.txt", "sha256": self.note_sha, "purpose": "synthetic fixture text"}
            ],
            "source_refs": [
                {"locator": "repo README", "kind": "doc", "coverage": "structure only"}
            ],
            "known_facts": [
                {"statement": "templates are empty", "evidence": ["inputs/note.txt"]}
            ],
            "hypotheses": [],
            "ruled_out": [],
            "requested_output": ["structure check result"],
            "limits": {
                "wall_seconds": 600,
                "max_tool_calls": 0,
                "max_cost_usd": 0,
                "max_output_bytes": 65536,
            },
            "data_policy": {
                "classification": "internal",
                "permission_ref": "perm-format-only",
                "audience": ["cc"],
            },
            "handoff": {"parent_ref": "SRV-0011/RUN-0010", "result_prefix": "results/"},
        }
        doc.update(overrides)
        return doc

    def result(self, **overrides):
        doc = {
            "schema": "research-result.v1",
            "request_id": "req-0001-demo",
            "result_id": "res-0001",
            "input_revision": REV,
            "status": "PARTIAL",
            "answers": [
                {
                    "question": "Does the validator reject an empty packet template?",
                    "answer": "Yes.",
                    "evidence": ["inputs/note.txt"],
                }
            ],
            "source_refs": [],
            "limitations": ["fixture only"],
            "provider": "synthetic",
            "report_path": "results/res-0001/report.md",
        }
        doc.update(overrides)
        return doc

    def write_packet(self, doc=None):
        path = self.base / "packet.json"
        path.write_text(json.dumps(doc if doc is not None else self.packet()), encoding="utf-8")
        return path

    def write_result(self, doc=None):
        path = self.base / "results" / "res-0001" / "result.json"
        path.write_text(json.dumps(doc if doc is not None else self.result()), encoding="utf-8")
        return path

    # -- tests ------------------------------------------------------------- #
    def test_valid_packet_passes(self):
        report = check_packet.check(self.write_packet())
        self.assertTrue(report["ok"], report["errors"])
        self.assertTrue(report["checks"]["packet_schema"])
        self.assertTrue(report["checks"]["packet_inputs"])
        self.assertFalse(report["checks"]["authorization_asserted"])
        self.assertFalse(report["checks"]["redaction_verified"])

    def test_null_packet_and_nonobject_result_are_rejected_without_crashing(self):
        packet = self.write_packet()
        packet.write_text("null")
        self.assertFalse(check_packet.check(packet)["ok"])
        packet = self.write_packet()
        result = self.write_result()
        result.write_text("[]")
        report = check_packet.check(packet, result, REV)
        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"]["version_match_verified"])

    def test_manifest_symlinks_and_foreign_result_files_are_rejected(self):
        packet, result = self.write_packet(), self.write_result()
        link = self.base / "linked.json"
        link.symlink_to(packet)
        self.assertFalse(check_packet.check(link)["ok"])
        outside = Path(self._tmp.name) / "result.json"
        outside.write_bytes(result.read_bytes())
        self.assertFalse(check_packet.check(packet, outside, REV)["ok"])

    def test_result_report_cannot_borrow_another_results_identity(self):
        packet = self.write_packet()
        result = self.write_result(self.result(result_id="another-result"))
        self.assertFalse(check_packet.check(packet, result, REV)["ok"])

    def test_empty_template_packet_rejected(self):
        template = json.loads((ROOT / "templates" / "packet.json").read_text(encoding="utf-8"))
        report = check_packet.check(self.write_packet(template))
        self.assertFalse(report["ok"])
        joined = " ".join(report["errors"])
        self.assertIn("request_id", joined)
        self.assertIn("questions", joined)
        self.assertIn("inputs", joined)
        self.assertIn("limits", joined)

    def test_empty_template_result_rejected(self):
        template = json.loads((ROOT / "templates" / "result.json").read_text(encoding="utf-8"))
        report = check_packet.check(
            self.write_packet(), self.write_result(template), REV
        )
        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"]["result_schema"])

    def test_wrong_input_hash_rejected(self):
        doc = self.packet(inputs=[{"path": "inputs/note.txt", "sha256": "0" * 64, "purpose": "synthetic"}])
        report = check_packet.check(self.write_packet(doc))
        self.assertFalse(report["ok"])
        self.assertIn("sha256 mismatch", " ".join(report["errors"]))

    def test_absolute_input_path_rejected(self):
        doc = self.packet(inputs=[{"path": "/etc/hostname", "sha256": self.note_sha, "purpose": "synthetic"}])
        report = check_packet.check(self.write_packet(doc))
        self.assertFalse(report["ok"])
        self.assertIn("absolute", " ".join(report["errors"]))

    def test_parent_traversal_rejected(self):
        doc = self.packet(inputs=[{"path": "../secret.txt", "sha256": self.note_sha, "purpose": "synthetic"}])
        report = check_packet.check(self.write_packet(doc))
        self.assertFalse(report["ok"])
        self.assertIn("..", " ".join(report["errors"]))

    def test_symlink_component_rejected(self):
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        (outside / "real.txt").write_text(NOTE_TEXT, encoding="utf-8")
        link = self.base / "inputs" / "link.txt"
        try:
            os.symlink(outside / "real.txt", link)
        except (OSError, NotImplementedError) as exc:  # pragma: no cover
            self.skipTest(f"symlinks unavailable: {exc}")
        doc = self.packet(inputs=[{"path": "inputs/link.txt", "sha256": self.note_sha, "purpose": "synthetic"}])
        report = check_packet.check(self.write_packet(doc))
        self.assertFalse(report["ok"])
        self.assertIn("symlink", " ".join(report["errors"]))

    def test_valid_result_passes(self):
        report = check_packet.check(self.write_packet(), self.write_result(), REV)
        self.assertTrue(report["ok"], report["errors"])
        self.assertTrue(report["checks"]["result_schema"])
        self.assertTrue(report["checks"]["result_references"])
        self.assertTrue(report["checks"]["version_match_verified"])
        self.assertTrue(report["checks"]["report_path"])

    def test_result_wrong_request_rejected(self):
        report = check_packet.check(
            self.write_packet(), self.write_result(self.result(request_id="req-9999-other")), REV
        )
        self.assertFalse(report["ok"])
        self.assertIn("request_id", " ".join(report["errors"]))

    def test_result_wrong_revision_rejected(self):
        report = check_packet.check(self.write_packet(), self.write_result(), OTHER_REV)
        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"]["version_match_verified"])
        self.assertIn("input_revision", " ".join(report["errors"]))

    def test_result_without_revision_not_verified(self):
        report = check_packet.check(self.write_packet(), self.write_result())
        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"]["version_match_verified"])
        self.assertIn("--input-revision is required", " ".join(report["errors"]))

    def test_report_path_outside_results_rejected(self):
        bad = self.result(report_path="inputs/note.txt")
        report = check_packet.check(self.write_packet(), self.write_result(bad), REV)
        self.assertFalse(report["ok"])
        self.assertIn("results/", " ".join(report["errors"]))

    def test_report_path_missing_file_rejected(self):
        bad = self.result(report_path="results/res-0001/missing.md")
        report = check_packet.check(self.write_packet(), self.write_result(bad), REV)
        self.assertFalse(report["ok"])
        self.assertIn("does not exist", " ".join(report["errors"]))

    def test_budget_non_positive_wall_rejected(self):
        doc = self.packet(limits={"wall_seconds": 0, "max_tool_calls": 0, "max_cost_usd": 0, "max_output_bytes": 1})
        report = check_packet.check(self.write_packet(doc))
        self.assertFalse(report["ok"])
        self.assertIn("wall_seconds", " ".join(report["errors"]))

    def test_budget_non_positive_output_rejected(self):
        doc = self.packet(limits={"wall_seconds": 1, "max_tool_calls": 0, "max_cost_usd": 0, "max_output_bytes": 0})
        report = check_packet.check(self.write_packet(doc))
        self.assertFalse(report["ok"])
        self.assertIn("max_output_bytes", " ".join(report["errors"]))

    def test_budget_negative_tool_calls_rejected(self):
        doc = self.packet(limits={"wall_seconds": 1, "max_tool_calls": -1, "max_cost_usd": 0, "max_output_bytes": 1})
        report = check_packet.check(self.write_packet(doc))
        self.assertFalse(report["ok"])
        self.assertIn("max_tool_calls", " ".join(report["errors"]))

    def test_budget_boolean_as_integer_rejected(self):
        doc = self.packet(limits={"wall_seconds": 1, "max_tool_calls": True, "max_cost_usd": 0, "max_output_bytes": 1})
        report = check_packet.check(self.write_packet(doc))
        self.assertFalse(report["ok"])
        self.assertIn("max_tool_calls", " ".join(report["errors"]))

    def test_budget_nan_rejected(self):
        path = self.base / "packet.json"
        text = json.dumps(self.packet())
        # Re-introduce a JSON NaN literal (json.dumps would emit it only via allow_nan).
        text = text.replace('"wall_seconds": 600', '"wall_seconds": NaN')
        path.write_text(text, encoding="utf-8")
        report = check_packet.check(path)
        self.assertFalse(report["ok"])
        self.assertIn("non-finite", " ".join(report["errors"]))

    def test_authorization_claim_property_rejected(self):
        # A packet may not smuggle an authorization flag past structural checks.
        doc = self.packet(authorized=True)
        report = check_packet.check(self.write_packet(doc))
        self.assertFalse(report["ok"])
        self.assertIn("authorized", " ".join(report["errors"]))

    def test_cli_exit_codes(self):
        good = self.write_packet()
        result = self.write_result()
        script = ROOT / "tools" / "check_packet.py"

        ok_run = subprocess.run(
            [sys.executable, str(script), "--packet", str(good), "--result", str(result), "--input-revision", REV],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(ok_run.returncode, 0, ok_run.stderr)
        self.assertTrue(json.loads(ok_run.stdout)["ok"])

        bad = self.write_packet(self.packet(inputs=[]))
        bad_run = subprocess.run(
            [sys.executable, str(script), "--packet", str(bad)],
            capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(bad_run.returncode, 0)
        self.assertFalse(json.loads(bad_run.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
