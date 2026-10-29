#!/usr/bin/env python3
"""Wiring #482's activation wrapper to #481's Batch CLI — the mapping, and its floor.

WHY THIS FILE EXISTS
--------------------
#481 published a real bounded CLI. It is not the shape #482's wrapper takes: it
wants argv and its `_run` returns `(exit_code, document)`, while a monitor entry is
a zero-argument callable returning a mapping. Calling `cli.main` through the
wrapper would either crash or return an exit code that says nothing about the
batch.

So there is one adapter, and this file pins what it may do: build argv from
operator configuration only, read the CLI's document once, and **carry the CLI's
word through** instead of summarising it into something that looks better.

The floor is the owner's rule: a task that no run ever reached, or whose summary
was never delivered, is **never green** — and the wrapper's own `BOUNDED_STOP`
is a different word from any of the CLI's states, so "the process finished" can
never be read as "the batch settled".
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADAPTER_PATH = (REPO / "integrations" / "fleet-next-agent-monitor-activation"
                / "batch_adapter.py")
ACTIVATION_PATH = (REPO / "integrations" / "fleet-next-agent-monitor-activation"
                   / "activation.py")
CLI_PATH = REPO / "integrations" / "fleet-v1-milestone-batch" / "cli.py"

ADAPTER_NAME = "serveros_next_monitor_batch_adapter"
ACTIVATION_NAME = "serveros_next_monitor_activation_for_batch"


def _load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


a = _load(ADAPTER_NAME, ADAPTER_PATH)
activation = _load(ACTIVATION_NAME, ACTIVATION_PATH)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="next-monitor-batch-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def config(self, **overrides):
        fields = dict(
            cli_path=str(CLI_PATH),
            registration=str(self.tmp / "batch.json"),
            ledger=str(self.tmp / "ledger.sqlite"),
            state=str(self.tmp / "state.json"),
            lock=str(self.tmp / "monitor.lock"),
            provider_factory="tests.test_fleet_v1_milestone_batch_cli_fakes:file_lark",
            source_factory="tests.test_fleet_v1_milestone_batch_cli_fakes:scripted_source")
        fields.update(overrides)
        return a.BatchCliConfig(**fields)


class ItRefusesToRunWithoutTheCli(Base):
    def test_a_missing_cli_path_is_blocked_runtime_import(self):
        config = self.config(cli_path=str(self.tmp / "no-cli.py"))
        with self.assertRaises(a.AdapterRefused) as caught:
            a.load_cli(config)
        self.assertEqual(caught.exception.code, a.BLOCKED_RUNTIME_IMPORT)

    def test_a_relative_cli_path_is_blocked_runtime_import(self):
        config = self.config(cli_path="integrations/x/cli.py")
        with self.assertRaises(a.AdapterRefused) as caught:
            a.tick(config)
        self.assertEqual(caught.exception.code, a.BLOCKED_RUNTIME_IMPORT)

    def test_a_cli_without_run_is_blocked_runtime_import(self):
        stub = self.tmp / "stub_cli.py"
        stub.write_text("def main(argv=None):\n    return 0\n", encoding="utf-8")
        with self.assertRaises(a.AdapterRefused) as caught:
            a.load_cli(self.config(cli_path=str(stub)))
        self.assertEqual(caught.exception.code, a.BLOCKED_RUNTIME_IMPORT)
        self.assertIn("_run", caught.exception.why)

    def test_a_cli_that_raises_on_import_is_blocked_runtime_import(self):
        stub = self.tmp / "boom_cli.py"
        stub.write_text("raise RuntimeError('no deps here')\n", encoding="utf-8")
        with self.assertRaises(a.AdapterRefused) as caught:
            a.load_cli(self.config(cli_path=str(stub)))
        self.assertEqual(caught.exception.code, a.BLOCKED_RUNTIME_IMPORT)
        self.assertIn("no deps here", caught.exception.why)


class TheArgvComesFromTheOperatorAndNowhereElse(Base):
    def test_the_argv_carries_exactly_the_configured_values(self):
        config = self.config()
        argv = a.argv_for(config)
        self.assertEqual(argv[:2], ["--registration", str(self.tmp / "batch.json")])
        for flag in ("--ledger", "--state", "--provider-factory",
                     "--source-factory"):
            self.assertIn(flag, argv)
        self.assertIn("--lock", argv)

    def test_a_factory_spec_that_is_not_module_colon_callable_is_refused(self):
        for bad in ("not-a-spec", "module:", ":callable", "module:attr.more"):
            with self.subTest(spec=bad):
                with self.assertRaises(a.AdapterRefused):
                    a.argv_for(self.config(provider_factory=bad))

    def test_the_adapter_reads_no_module_name_out_of_the_registration(self):
        """The hole #481 closed stays closed from this side too."""
        source = ADAPTER_PATH.read_text(encoding="utf-8")
        for forbidden in ("json.load", "json.loads", "--import", "exec(",
                          "import_module("):
            self.assertNotIn(
                forbidden, source,
                f"{forbidden!r} in the adapter is a path from Batch JSON into "
                "a Python import")

    def test_only_the_cli_path_is_loaded_dynamically(self):
        source = ADAPTER_PATH.read_text(encoding="utf-8")
        self.assertEqual(source.count("spec_from_file_location"), 1,
                         "one dynamic load, and it is the operator's cli_path")


class TheCliWordIsCarriedNotSummarised(Base):
    def _row(self, document, exit_code=0):
        return a.row_from(document, exit_code)

    def test_each_cli_state_is_preserved(self):
        for state in sorted(a.CLI_STATES):
            with self.subTest(state=state):
                row = self._row({"state": state, "summary": None})
                self.assertEqual(row["state"], state)
                self.assertEqual(row["cli_state"], state)

    def test_no_cli_state_is_ever_the_wrappers_own_word(self):
        """`BOUNDED_STOP` is the wrapper finishing; it is not a batch verdict."""
        for state in sorted(a.CLI_STATES):
            with self.subTest(state=state):
                row = self._row({"state": state, "summary": None})
                self.assertNotEqual(row["state"], activation.BOUNDED_STOP)

    def test_an_unrecognised_state_fails_closed(self):
        row = self._row({"state": "PROBABLY_FINE", "summary": None})
        self.assertEqual(row["state"], a.UNRECOGNISED)
        self.assertFalse(row["green"])

    def test_a_document_that_is_not_a_mapping_fails_closed(self):
        stub = self.tmp / "list_cli.py"
        stub.write_text(
            "def _run(argv):\n    return 0, ['not', 'a', 'document']\n",
            encoding="utf-8")
        row = a.tick(self.config(cli_path=str(stub)))
        self.assertEqual(row["state"], a.UNRECOGNISED)
        self.assertFalse(row["green"])


class GreenIsATerminalBatchWithAConfirmedSummary(Base):
    def test_settled_with_a_delivered_summary_is_green(self):
        self.assertTrue(a.is_green("SETTLED", "DELIVERED"))
        self.assertTrue(a.is_green("SETTLED", "DUPLICATE"))

    def test_settled_without_a_delivered_summary_is_never_green(self):
        for summary in (None, "DELIVERY_UNKNOWN", "UNKNOWN", "CARD_ONLY"):
            with self.subTest(summary=summary):
                self.assertFalse(a.is_green("SETTLED", summary),
                                 "a settled batch whose summary did not reach the "
                                 "provider is not a delivered milestone")

    def test_ticked_is_never_green(self):
        """ACK-only members land here: progress, not success."""
        self.assertFalse(a.is_green("TICKED", "DELIVERED"))

    def test_blocked_input_is_never_green(self):
        """No member has a run: the dispatch is stuck, not done."""
        self.assertFalse(a.is_green("BLOCKED_INPUT", None))
        self.assertFalse(a.is_green("BLOCKED_INPUT", "DELIVERED"))

    def test_unconfirmed_is_never_green(self):
        self.assertFalse(a.is_green("UNCONFIRMED", "DELIVERY_UNKNOWN"))

    def test_a_blocked_row_counts_no_undelivered_receipt(self):
        """Nothing was sent, so there is no receipt missing."""
        row = a.row_from({"state": "BLOCKED_INPUT", "summary": None}, 3)
        self.assertEqual(row["unknown_receipts"], 0)

    def test_an_unconfirmed_row_counts_one_undelivered_receipt(self):
        row = a.row_from({"state": "UNCONFIRMED",
                          "summary": {"state": "DELIVERY_UNKNOWN"}}, 4)
        self.assertEqual(row["unknown_receipts"], 1)


class TheWrapperKeepsTheTwoWordsApart(Base):
    """`BOUNDED_STOP` is the process finishing; the batch word is the batch.

    #495 measured what happens when these are merged: a `TICKED` or
    `BLOCKED_INPUT` run reached a supervisor as `BOUNDED_STOP` / exit 0, so the
    wrapper's own stop was read as the batch's completion. So the wrapper now
    reports the batch's word, and `BOUNDED_STOP` is reserved for a **green** row —
    which is the only case in which the two words genuinely agree.
    """

    def _activation(self, ticks=1):
        return activation.Activation(
            legacy_root=str(self.tmp / "legacy"), next_root=str(self.tmp / "next"),
            state=str(self.tmp / "next" / "state"),
            lock=str(self.tmp / "next" / "monitor.lock"), port=3241,
            legacy_port=3231, service_name="serveros-next-agent-monitor",
            legacy_service_name="serveros-fleet-node-connector",
            plugin_id="serveros-fleet-next-ingress",
            legacy_plugin_id="serveros-fleet-ingress", ticks=ticks)

    def _through_the_wrapper(self, document, exit_code=0):
        stub = self.tmp / "cli_under_test.py"
        stub.write_text(
            "def _run(argv):\n"
            f"    return {exit_code}, {document!r}\n", encoding="utf-8")
        config = self.config(cli_path=str(stub))
        return activation.run_bounded(
            self._activation(),
            monitor_entry=lambda: a.tick(config), legacy_read=lambda: True,
            next_write=lambda: False, clock=lambda: 0.0)

    def test_a_ticked_batch_is_reported_as_ticked_not_as_a_bounded_stop(self):
        result = self._through_the_wrapper(
            {"state": "TICKED", "batch_id": "b-1", "summary": None})
        self.assertEqual(result["state"], "TICKED")
        self.assertIs(result["completed"], False)
        self.assertNotEqual(result["state"], activation.BOUNDED_STOP,
                            "a batch still in flight finished the process, not "
                            "the batch")

    def test_a_blocked_batch_reaches_the_supervisor_with_its_own_exit(self):
        result = self._through_the_wrapper(
            {"state": "BLOCKED_INPUT", "summary": None}, exit_code=3)
        self.assertEqual(result["state"], "BLOCKED_INPUT")
        self.assertEqual(result["exit"], 3)
        self.assertIs(result["completed"], False)

    def test_a_settled_and_delivered_batch_is_the_only_bounded_stop(self):
        result = self._through_the_wrapper(
            {"state": "SETTLED", "batch_id": "b-2",
             "summary": {"state": "DELIVERED"}})
        self.assertEqual(result["state"], activation.BOUNDED_STOP)
        self.assertIs(result["completed"], True)
        self.assertEqual(result["ticks_report"][0]["state"], "SETTLED",
                         "the batch's own word is still carried underneath")

    def test_a_blocked_cli_still_lets_the_wrapper_report_its_own_health(self):
        stub = self.tmp / "blocked_cli.py"
        stub.write_text(
            "def _run(argv):\n"
            "    return 3, {'state': 'BLOCKED_INPUT', 'summary': None,"
            " 'code': 'NO_ISSUED_RUN'}\n",
            encoding="utf-8")
        config = self.config(cli_path=str(stub))
        row = a.tick(config)
        self.assertEqual(row["state"], "BLOCKED_INPUT")
        self.assertFalse(row["green"])
        self.assertEqual(row["why"], "NO_ISSUED_RUN")


class AcrossTwoInterpreters(unittest.TestCase):
    """The same adapter, in separate processes, over one shared temp directory."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="next-monitor-xproc-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _run_once(self, cli_path):
        script = (
            "import importlib.util, json, sys\n"
            f"spec = importlib.util.spec_from_file_location('ad', r'{ADAPTER_PATH}')\n"
            "m = importlib.util.module_from_spec(spec); sys.modules['ad'] = m\n"
            "spec.loader.exec_module(m)\n"
            "cfg = m.BatchCliConfig(cli_path=sys.argv[1],\n"
            f"    registration=r'{os_join(self.tmp, 'batch.json')}',\n"
            f"    ledger=r'{os_join(self.tmp, 'ledger.sqlite')}',\n"
            f"    state=r'{os_join(self.tmp, 'state.json')}',\n"
            "    provider_factory='m:callable', source_factory='m:callable')\n"
            "row = m.tick(cfg)\n"
            "json.dump({'state': row['state'], 'green': row['green']}, sys.stdout)\n")
        return subprocess.run([sys.executable, "-I", "-c", script, str(cli_path)],
                              text=True, capture_output=True, timeout=120)

    def test_two_processes_agree_and_neither_invents_a_green(self):
        stub = self.tmp / "stable_cli.py"
        stub.write_text(
            "def _run(argv):\n"
            "    return 3, {'state': 'BLOCKED_INPUT', 'summary': None}\n",
            encoding="utf-8")
        first = self._run_once(stub)
        second = self._run_once(stub)
        self.assertEqual(first.returncode, 0, first.stderr[-400:])
        self.assertEqual(second.returncode, 0, second.stderr[-400:])
        one, two = json.loads(first.stdout), json.loads(second.stdout)
        self.assertEqual(one, two, "two processes disagreed about one document")
        self.assertEqual(one["state"], "BLOCKED_INPUT")
        self.assertFalse(one["green"],
                         "a dispatch no run ever reached became green in a "
                         "second process")

    def test_a_missing_cli_in_a_fresh_process_is_blocked_not_empty(self):
        result = self._run_once(self.tmp / "absent.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BLOCKED_RUNTIME_IMPORT", result.stderr)


class TheRealCliThroughTheAdapterAcrossProcesses(unittest.TestCase):
    """#481's actual `cli.py`, driven by this adapter, in separate interpreters.

    Not a stub: the registration is filed with #481's own `registration` helper
    and the CLI is executed for real. The scenario is the one the owner named —
    a dispatch whose ACKs carried no `run_id` and whose deadline is still ahead,
    which must be `BLOCKED_INPUT`: nothing read, nothing sent, and never green.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="next-monitor-real-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        try:
            self.e2e = _load("next_monitor_e2e_support",
                             REPO / "tests" / "test_fleet_v1_milestone_batch_e2e_integration.py")
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"#481's e2e harness is not loadable here: {exc}")
        if not CLI_PATH.is_file():
            self.skipTest("#481's cli.py is not in this tree")

    def _file_ack_only(self) -> Path:
        """Every member accepted, none with a run. The deadline is still ahead."""
        acks = self.e2e.acks()
        for row in acks:
            row.pop("run_id", None)
        path = self.tmp / "batch.json"
        filed, outcome = self.e2e.registration.from_commander(
            self.e2e.plan(), acks, contract=self.e2e.Commander(),
            snapshot={"devices": {}}, source="cloudcli",
            registered_at=self.e2e.REGISTERED_AT,
            conversation_ref=self.e2e.RECIPIENT,
            # A FUTURE deadline, not the harness constant: `e2e.DEADLINE` is
            # 2026-10-27T11:00Z, which is already past, and a passed deadline
            # legitimately settles the batch. Using it would have made this test
            # report a defect in #481 that does not exist.
            expires_at="2027-01-01T00:00:00Z", path=path)
        self.assertEqual(outcome, self.e2e.registration.REGISTERED,
                         "the ACK-only registration must file for this to mean anything")
        return path

    def _tick_in_a_new_process(self, registration: Path):
        journal = self.tmp / "provider.jsonl"
        provider_options = self.tmp / "provider.json"
        provider_options.write_text(json.dumps({"journal": str(journal),
                                                "closed": str(self.tmp / "closed.jsonl")}),
                                    encoding="utf-8")
        source_options = self.tmp / "source.json"
        source_options.write_text(json.dumps({"script": {}, "reads": str(self.tmp / "reads.jsonl")}),
                                  encoding="utf-8")
        script = (
            "import importlib.util, json, sys\n"
            f"sys.path.insert(0, r'{REPO}')\n"
            f"spec = importlib.util.spec_from_file_location('ad', r'{ADAPTER_PATH}')\n"
            "m = importlib.util.module_from_spec(spec); sys.modules['ad'] = m\n"
            "spec.loader.exec_module(m)\n"
            "cfg = m.BatchCliConfig(\n"
            f"    cli_path=r'{CLI_PATH}',\n"
            f"    registration=sys.argv[1],\n"
            f"    ledger=r'{os_join(self.tmp, 'ledger.sqlite')}',\n"
            f"    state=r'{os_join(self.tmp, 'state.json')}',\n"
            f"    lock=r'{os_join(self.tmp, 'monitor.lock')}',\n"
            "    provider_factory='tests.test_fleet_v1_milestone_batch_cli_fakes:file_lark',\n"
            "    source_factory='tests.test_fleet_v1_milestone_batch_cli_fakes:scripted_source',\n"
            f"    provider_options=r'{provider_options}',\n"
            f"    source_options=r'{source_options}')\n"
            "row = m.tick(cfg)\n"
            "json.dump({'state': row['state'], 'exit': row['exit'],"
            " 'green': row['green'], 'why': row.get('why')}, sys.stdout)\n")
        return subprocess.run([sys.executable, "-I", "-c", script, str(registration)],
                              text=True, capture_output=True, timeout=180)

    def test_an_ack_only_dispatch_is_blocked_and_never_green_in_two_processes(self):
        registration = self._file_ack_only()
        first = self._tick_in_a_new_process(registration)
        second = self._tick_in_a_new_process(registration)
        for result in (first, second):
            self.assertEqual(result.returncode, 0,
                             f"adapter crashed: {result.stderr[-600:]}")
        one = json.loads(first.stdout)
        two = json.loads(second.stdout)
        self.assertEqual(one["state"], "BLOCKED_INPUT",
                         f"an ACK-only dispatch is not settled: {one}")
        self.assertEqual(one["exit"], 3)
        self.assertFalse(one["green"])
        self.assertEqual(one, two,
                         "two interpreters disagreed about one registration")
        journal = self.tmp / "provider.jsonl"
        self.assertEqual(journal.read_text(encoding="utf-8") if journal.exists() else "",
                         "", "a blocked dispatch must not have sent anything")


def os_join(base, name):
    return str(Path(base) / name)


if __name__ == "__main__":
    unittest.main()
