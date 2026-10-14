"""The wildcard-binding preflight: what it refuses to work out, and what it writes.

The thing under test is mostly an absence. A migration from `send_allow: ['*']`
to `operator_grants` cannot be performed by a tool, because a wildcard recorded
no principals and nothing in the binding says who used it. The valuable output is
therefore a worklist that names what a human still has to decide — and the
failures worth testing are the ways a tool could quietly do more than that:

* inventing a principal so the migration looks finished;
* turning "all sessions" into "all principals", which is the same wildcard one
  dimension over;
* widening a migration to verbs the operator never asked for;
* writing the binding, or restarting the unit, when the whole instruction was to
  look and report;
* carrying a credential out of a file it read into a document an operator may
  paste into a ticket.

Each of those is a test below. The canary test is the one that would be easiest
to pass by accident with a redaction filter and hardest to pass by never reading
the value in the first place, which is what this module does.
"""
from __future__ import annotations

import ast
import copy
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp.deploy import binding_migration as bm  # noqa: E402

WILDCARD_FIXTURE = ROOT / "tests" / "data" / "fleet-binding-wildcard.sample.yaml"
MIGRATED_FIXTURE = ROOT / "tests" / "data" / "fleet-binding-migrated.sample.yaml"
MODULE = ROOT / "fleet_mcp" / "deploy" / "binding_migration.py"


def _copy_fixture(test: unittest.TestCase, fixture: Path = WILDCARD_FIXTURE) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="fleet-bind-"))
    test.addCleanup(shutil.rmtree, tmp, True)
    target = tmp / fixture.name
    shutil.copy2(fixture, target)
    return target


def _stamp(path: Path) -> tuple[bytes, int, int]:
    stat = path.stat()
    return path.read_bytes(), stat.st_mtime_ns, stat.st_size


class TheOldBindingIsReportedNotRewritten(unittest.TestCase):

    def test_the_wildcard_binding_is_refused_by_this_branch_and_says_so(self):
        report = bm.preflight(WILDCARD_FIXTURE)
        self.assertFalse(report["loader"]["loads"])
        self.assertEqual(report["loader"]["code"], "BAD_REQUEST")
        # The refusal is the loader's own words, not a paraphrase: an operator
        # who reads the report and then the service log must see one sentence,
        # not two that disagree.
        self.assertIn("allow_wildcard_send", report["loader"]["detail"])

    def test_the_verdict_is_needs_human_and_not_a_completed_migration(self):
        report = bm.preflight(WILDCARD_FIXTURE)
        self.assertEqual(report["verdict"], bm.NEEDS_HUMAN)
        self.assertEqual(
            sorted((row["device_ref"], row["verb"]) for row in report["principals_needed"]),
            [("device:llm-lab-dell", "send"),
             ("device:tokyo-shared-dev-01", "send"),
             ("device:work-fj", "send")],
        )

    def test_no_principal_is_ever_invented_from_a_wildcard(self):
        report = bm.preflight(WILDCARD_FIXTURE)
        self.assertEqual(report["principals_already_named"], 0)
        template = report["template"]
        self.assertIn(bm.PRINCIPAL_PLACEHOLDER, template)
        # Every principal-shaped key in the template is the placeholder. A real
        # name here would be the tool answering a question only a human can.
        grants = yaml.safe_load(template)["policy"]["operator_grants"]
        self.assertEqual(list(grants), [bm.PRINCIPAL_PLACEHOLDER])

    def test_the_wildcard_never_becomes_a_principal_or_a_default_allow(self):
        report = bm.preflight(WILDCARD_FIXTURE)
        granted = yaml.safe_load(report["template"])["policy"]["operator_grants"]
        principal = next(iter(granted))
        self.assertNotEqual(principal, bm.WILDCARD)
        self.assertNotIn(bm.WILDCARD, principal)
        self.assertNotIn(bm.WILDCARD, json.dumps(granted))

    def test_a_migration_is_not_widened_to_verbs_the_binding_never_asked_for(self):
        report = bm.preflight(WILDCARD_FIXTURE)
        # The fixture sets `allow_send` only. create/control must be reported as
        # not-wanted, never as something a human must now grant.
        self.assertEqual(
            {row["verb"] for row in report["principals_needed"]}, {"send"})
        for row in report["devices"]:
            if row["verb"] in ("create", "control"):
                self.assertEqual(row["status"], bm.NOT_WANTED)

    def test_a_device_the_registry_calls_personal_is_deferred_not_granted(self):
        report = bm.preflight(WILDCARD_FIXTURE)
        personal = [row for row in report["devices"]
                    if row["device_ref"] == "device:personal-dell"]
        self.assertTrue(personal)
        for row in personal:
            self.assertNotEqual(row["status"], bm.NEEDS_HUMAN)
            self.assertNotIn(row["device_ref"],
                             [entry["device_ref"] for entry in report["principals_needed"]])
        send_row = next(row for row in personal if row["verb"] == "send")
        self.assertEqual(send_row["status"], bm.DEFER)
        self.assertEqual(send_row["ownership"], "personal")

    def test_an_unreadable_registry_defers_rather_than_guessing_eligibility(self):
        report = bm.preflight(WILDCARD_FIXTURE, registry_path="/nonexistent/registry.yaml")
        for row in report["devices"]:
            if row["device_flag_set"]:
                self.assertEqual(row["status"], bm.DEFER)
                self.assertEqual(row["ownership"], "UNKNOWN")


class TheMigrationIsReportedAsFinishedOnlyWhenItIs(unittest.TestCase):

    def test_a_named_binding_reports_already_migrated(self):
        report = bm.preflight(MIGRATED_FIXTURE)
        self.assertEqual(report["verdict"], bm.ALREADY_MIGRATED)
        self.assertEqual(report["principals_needed"], [])
        self.assertEqual(report["template"], "")
        self.assertTrue(report["loader"]["loads"])

    def test_a_binding_that_grants_nothing_is_not_called_migrated(self):
        path = _copy_fixture(self)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        for entry in raw["providers"].values():
            entry.pop("allow_send", None)
        raw["policy"] = {}
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        report = bm.preflight(path)
        self.assertEqual(report["verdict"], bm.DEFER)
        self.assertNotEqual(report["verdict"], bm.ALREADY_MIGRATED)

    def test_a_missing_binding_is_refused(self):
        from fleet_mcp.errors import FleetRefusal
        with self.assertRaises(FleetRefusal):
            bm.preflight("/nonexistent/binding.yaml")


class NothingIsWrittenAndNothingIsRestarted(unittest.TestCase):

    def test_the_binding_is_byte_and_mtime_identical_after_a_run(self):
        # Byte equality alone would pass a rewrite that wrote the same bytes.
        # The mtime is what catches a write that changed nothing.
        path = _copy_fixture(self)
        before = _stamp(path)
        bm.preflight(path)
        bm.render(bm.preflight(path))
        self.assertEqual(_stamp(path), before)

    def test_the_cli_writes_nothing_either(self):
        path = _copy_fixture(self)
        before = _stamp(path)
        self.assertEqual(bm.main(["--binding", str(path)]), 0)
        self.assertEqual(bm.main(["--binding", str(path), "--json"]), 0)
        self.assertEqual(_stamp(path), before)

    def test_every_report_declares_no_writes_and_no_restarts(self):
        for fixture in (WILDCARD_FIXTURE, MIGRATED_FIXTURE):
            with self.subTest(fixture=fixture.name):
                report = bm.preflight(fixture)
                self.assertEqual(report["writes"], [])
                self.assertEqual(report["restarts"], [])
                self.assertEqual(report["mode"], "DRY_RUN")

    def test_the_module_has_no_write_or_restart_path_in_its_code(self):
        """A static check, because a claim about a tool's reach should not rest
        on reading its prose. Parsed, not grepped: the docstring says the words
        "subprocess" and "systemctl" on purpose, and a text search would fail on
        the sentence that promises their absence."""
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        imported: set[str] = set()
        called: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    called.add(func.id)
                elif isinstance(func, ast.Attribute):
                    called.add(func.attr)
        for forbidden in ("subprocess", "shutil", "systemctl"):
            self.assertNotIn(forbidden, imported | called)
        for write_call in ("write_text", "write_bytes", "safe_dump", "dump",
                           "system", "popen", "run"):
            self.assertNotIn(write_call, called)


class CredentialsDoNotLeaveTheFile(unittest.TestCase):

    CANARY = "CANARY-0f3a9d-must-not-appear-in-any-report"

    def _canaried(self) -> Path:
        path = _copy_fixture(self)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["auth"]["pat_anchor"] = f"/secrets/{self.CANARY}.token"
        for entry in raw["providers"].values():
            entry["credential_anchor"] = f"/secrets/{self.CANARY}.secret"
            entry["endpoint"] = f"http://user:{self.CANARY}@127.0.0.1:3402"
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        return path

    def test_a_canary_credential_never_reaches_the_json_report(self):
        report = bm.preflight(self._canaried())
        self.assertNotIn(self.CANARY, json.dumps(report))

    def test_a_canary_credential_never_reaches_the_rendered_report(self):
        report = bm.preflight(self._canaried())
        self.assertNotIn(self.CANARY, bm.render(report))

    def test_the_report_reads_an_allowlist_and_not_the_sensitive_keys(self):
        # The stronger claim: the values are not redacted out, they are never
        # read in. The report is built from fields this list names, so any key
        # outside it must be absent from the document entirely.
        report = bm.preflight(self._canaried())
        blob = json.dumps(report)
        for never_read in ("credential_anchor", "pat_anchor", "endpoint"):
            self.assertNotIn(never_read, blob)


class TheRenderingMatchesTheDocument(unittest.TestCase):

    def test_the_human_view_names_every_device_awaiting_a_decision(self):
        report = bm.preflight(WILDCARD_FIXTURE)
        text = bm.render(report)
        for entry in report["principals_needed"]:
            self.assertIn(entry["device_ref"], text)

    def test_the_json_report_is_parseable_and_carries_the_schema(self):
        report = bm.preflight(WILDCARD_FIXTURE)
        parsed = json.loads(json.dumps(report))
        self.assertEqual(parsed["schema"], bm.SCHEMA)

    def test_the_exit_code_reports_that_the_tool_ran_not_that_it_agreed(self):
        # A NEEDS_HUMAN verdict with a nonzero exit would read as "the tool
        # failed". The tool is the one part that worked.
        path = _copy_fixture(self)
        self.assertEqual(bm.main(["--binding", str(path)]), 0)


if __name__ == "__main__":
    unittest.main()
