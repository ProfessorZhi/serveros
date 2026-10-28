#!/usr/bin/env python3
"""Independent cross-PR acceptance for the Fleet Next Agent Monitor train.

The four inputs are pinned by SHA in ``integrations/fleet-next-monitor-crosspr-qa/
compose.py``. Nothing here imports a working tree's nearest copy: each exact
commit is extracted and put on ``sys.path`` side by side, and the imported
module's ``__file__`` is asserted to live under its own pinned checkout.

What this file is allowed to conclude
-------------------------------------
*   **CODE / UNIT / CI** are the layers exercised here. Every provider is a
    file-backed fake; no socket, credential, model or Feishu call is made.
*   **REAL_DEVICE / PRODUCTION** are **NOT_RUN**. No timer is installed, no
    systemd unit is enabled, no live Legacy client is contacted.

The blockers this run finds are written as:
*   a **characterisation test** that passes while pinning the observed defect
    (so CI keeps running the real code), and
*   an ``expectedFailure`` test that states the behaviour the interface owes.
    If an owner fixes the seam, that test becomes an unexpected success and
    fails, which is the signal to remove the exemption.

The headline blocker is in ``MonitorCore``: a delivery that #423 recorded as
``DELIVERY_UNKNOWN`` is reported as ``DUPLICATE`` on the next tick because the
monitor's cursor was written *before* the hand-over. Two ticks later the state
is ``SETTLED`` with no provider receipt. See
``docs/integration/FLEET-NEXT-MONITOR-CROSSPR-QA-20261028.md``.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
QA_DIR = REPO / "integrations" / "fleet-next-monitor-crosspr-qa"

if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))

import compose  # noqa: E402 - after the path is installed

DEV = "device:tokyo-qa-1"
DEV2 = "device:tokyo-qa-2"
SESS = "device:tokyo-qa-1|claude|sess-qa"
SESS2 = "device:tokyo-qa-2|claude|sess-qa-2"
RUN = "run-qa-1"
RUN2 = "run-qa-2"
DEL = "del-qa-1"
DEL2 = "del-qa-2"
BOOT = "boot-qa-1"
BOOT2 = "boot-qa-2"
SHA = "a" * 40
OTHER_SHA = "f" * 40
INSTANCE = "inst-qa"
ROUTE = "route-qa"

MONITOR_SCHEMA = "serveros/fleet-next-agent-monitor-config.v1"
STATE_SCHEMA = "serveros/fleet-next-agent-monitor-state.v1"

M: dict[str, object] = {}
COMPOSE_ROOT: Path | None = None
CHECKOUTS: dict[int, Path] = {}
ADDED_PATHS: list[str] = []


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def setUpModule() -> None:  # noqa: N802 - unittest protocol
    global COMPOSE_ROOT, CHECKOUTS, ADDED_PATHS
    COMPOSE_ROOT, CHECKOUTS = compose.materialize()
    ADDED_PATHS = compose.install(CHECKOUTS)

    monitor_package = CHECKOUTS[493] / compose.INTEGRATION_DIRS[493]
    if not monitor_package.is_dir():
        raise RuntimeError(
            "BASELINE_RED: integrations/fleet-next-agent-monitor is absent from "
            f"{CHECKOUTS[493]}; this is the pre-fix state, not a pass")

    import fleet_next_agent_monitor as monitor
    import fleet_next_agent_monitor.runtime as monitor_runtime
    import fleet_next_agent_monitor.contract as monitor_contract
    import milestone_notify
    import activation
    import batch_adapter
    import fleet_next_monitor_source_adapter as source_adapter
    import qa_fakes
    import source_bridge

    from fleet_next_agent_monitor.core import MonitorCore
    from fleet_next_agent_monitor.state import DurableState
    from fleet_next_agent_monitor.sources import observe_watch

    batch = _load("serveros_fleet_v1_milestone_batch",
                  CHECKOUTS[488] / compose.BATCH_DIR / "batch.py")
    registration = _load(
        "serveros_fleet_v1_milestone_batch_registration",
        CHECKOUTS[488] / compose.BATCH_DIR / "registration.py")

    M.update({
        "monitor": monitor,
        "runtime": monitor_runtime,
        "contract": monitor_contract,
        "core": MonitorCore,
        "state": DurableState,
        "observe_watch": observe_watch,
        "notify": milestone_notify,
        "activation": activation,
        "batch_adapter": batch_adapter,
        "source_adapter": source_adapter,
        "qa_fakes": qa_fakes,
        "source_bridge": source_bridge,
        "batch": batch,
        "registration": registration,
    })


def tearDownModule() -> None:  # noqa: N802 - unittest protocol
    for path in ADDED_PATHS:
        try:
            sys.path.remove(path)
        except ValueError:
            pass
    if COMPOSE_ROOT is not None and not os.environ.get("FLEET_QA_BASELINE_ROOT"):
        compose.remove(COMPOSE_ROOT)


def _assert_under(test: unittest.TestCase, module_file: str, root: Path) -> None:
    path = Path(module_file).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        test.fail(f"{path} is not under the pinned checkout {root}")


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("FLEET_NEXT_AGENT_MONITOR_"):
            env.pop(key)
    env.pop("FLEET_QA_BASELINE_ROOT", None)
    return env


def _composed_pythonpath() -> str:
    parts = [str(QA_DIR), *compose.integration_paths(CHECKOUTS),
             str(CHECKOUTS[488] / compose.BATCH_DIR)]
    return os.pathsep.join(parts)


def _iso(seconds_ago: float = 0.0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(time.time() - seconds_ago))


class Scenario:
    """One temp directory holding a config, facts, state, ledger and journal."""

    def __init__(self, test: unittest.TestCase) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="fleet-crosspr-scenario-"))
        test.addCleanup(shutil.rmtree, self.dir, True)
        self.legacy = self.dir / "legacy"
        self.next = self.dir / "next"
        self.legacy.mkdir()
        self.next.mkdir()
        self.facts_path = self.dir / "facts.json"
        self.config_path = self.dir / "config.json"
        self.state_path = self.next / "state.json"
        self.lock_path = self.next / "monitor.lock"
        self.ledger_path = self.dir / "ledger.sqlite"
        self.journal_path = self.dir / "journal.jsonl"
        self.batch_state = self.dir / "batch-state.json"
        self.batch_lock = self.dir / "batch.lock"

    # -- files -------------------------------------------------------------

    def write_facts(self, *, runs: dict | None = None,
                    ci: dict | None = None) -> None:
        self.facts_path.write_text(json.dumps({
            "fleet_instance_id": INSTANCE,
            "route_id": ROUTE,
            "boot_id": BOOT,
            "runs": runs or {},
            "ci": ci or {},
        }), encoding="utf-8")

    def write_config(self, watches: list[dict], *, views: list[str],
                     devices: list[str] | None = None,
                     sessions: list[str] | None = None,
                     projects: list[str] | None = None,
                     bounds: dict | None = None) -> None:
        self.config_path.write_text(json.dumps({
            "schema": MONITOR_SCHEMA,
            "grant": {
                "principal": "qa-crosspr",
                "devices": devices if devices is not None else [DEV, DEV2],
                "sessions": sessions if sessions is not None else [SESS, SESS2],
                "projects": projects or [],
                "views": views,
            },
            "watches": watches,
            "bounds": bounds or {"debounce_ticks": 1,
                                 "stale_after_seconds": 3600,
                                 "max_warnings": 8},
        }), encoding="utf-8")

    # -- environment -------------------------------------------------------

    def env(self, *, notifier: bool = True,
            extra: dict | None = None) -> dict[str, str]:
        env = _clean_env()
        env["PYTHONPATH"] = _composed_pythonpath()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["QA_JOURNAL"] = str(self.journal_path)
        env["QA_LEDGER"] = str(self.ledger_path)
        env["QA_RUN_ID"] = RUN
        env["QA_DEVICE"] = DEV
        env["QA_SESSION"] = SESS
        env["QA_BOOT"] = BOOT
        env["FLEET_NEXT_AGENT_MONITOR_CONFIG"] = str(self.config_path)
        env["FLEET_NEXT_AGENT_MONITOR_STATE"] = str(self.state_path)
        env["FLEET_NEXT_AGENT_MONITOR_OFFLINE_FACTS"] = str(self.facts_path)
        if notifier:
            env["FLEET_NEXT_AGENT_MONITOR_NOTIFIER"] = \
                "qa_fakes:notifier_factory"
        if extra:
            env.update(extra)
        return env

    # -- processes ---------------------------------------------------------

    def run_monitor(self, *, notifier: bool = True,
                    extra_env: dict | None = None,
                    timeout: float = 120.0) -> tuple[int, dict, subprocess.CompletedProcess]:
        args = [sys.executable, "-m", "fleet_next_agent_monitor",
                "--config", str(self.config_path),
                "--state", str(self.state_path),
                "--offline-facts", str(self.facts_path)]
        if notifier:
            args += ["--notifier-entry", "qa_fakes:notifier_factory"]
        done = subprocess.run(args, cwd=str(self.dir), env=self.env(
            notifier=notifier, extra=extra_env), capture_output=True,
            text=True, timeout=timeout)
        document = json.loads(done.stdout) if done.stdout.strip() else {}
        return done.returncode, document, done

    def run_activation(self, *, configured: bool = True, ticks: int = 1,
                       extra_env: dict | None = None,
                       timeout: float = 120.0) -> tuple[int, dict, subprocess.CompletedProcess]:
        roots = [str(QA_DIR), *compose.integration_paths(CHECKOUTS),
                 str(CHECKOUTS[488] / compose.BATCH_DIR)]
        args = [
            sys.executable, str(QA_DIR / "run_activation.py"),
            "--legacy-root", str(self.legacy),
            "--next-root", str(self.next),
            "--state", str(self.state_path),
            "--lock", str(self.lock_path),
            "--port", "19101", "--legacy-port", "19100",
            "--service-name", "serveros-next-agent-monitor",
            "--legacy-service-name", "serveros-legacy",
            "--plugin-id", "serveros-next-monitor",
            "--legacy-plugin-id", "serveros-legacy",
            "--ticks", str(ticks),
            "--monitor-entry", "fleet_next_agent_monitor.runtime:tick",
            "--legacy-read", "activation:probe_true",
            "--next-write", "activation:probe_false",
        ]
        env = _clean_env()
        env["PYTHONPATH"] = _composed_pythonpath()
        env["QA_ROOTS"] = json.dumps(roots)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["QA_JOURNAL"] = str(self.journal_path)
        env["QA_LEDGER"] = str(self.ledger_path)
        if configured:
            env.update({
                "FLEET_NEXT_AGENT_MONITOR_CONFIG": str(self.config_path),
                "FLEET_NEXT_AGENT_MONITOR_STATE": str(self.state_path),
                "FLEET_NEXT_AGENT_MONITOR_OFFLINE_FACTS": str(self.facts_path),
                "FLEET_NEXT_AGENT_MONITOR_NOTIFIER": "qa_fakes:notifier_factory",
            })
        if extra_env:
            env.update(extra_env)
        done = subprocess.run(args, cwd=str(self.dir), env=env,
                              capture_output=True, text=True, timeout=timeout)
        document = json.loads(done.stdout) if done.stdout.strip() else {}
        return done.returncode, document, done

    # -- read back ---------------------------------------------------------

    def journal(self) -> list[dict]:
        if not self.journal_path.exists():
            return []
        return [json.loads(line) for line in
                self.journal_path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def ledger_rows(self) -> list[dict]:
        if not self.ledger_path.exists():
            return []
        with sqlite3.connect(self.ledger_path) as db:
            db.row_factory = sqlite3.Row
            try:
                return [dict(row) for row in db.execute(
                    "SELECT * FROM milestone_deliveries ORDER BY delivery_key")]
            except sqlite3.OperationalError:
                return []

    def state_document(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))


def run_watch(**overrides) -> dict:
    watch = {"kind": "run", "device_ref": DEV, "session_ref": SESS,
             "run_id": RUN, "delivery_id": DEL,
             "expected_boot_id": BOOT, "major": True}
    watch.update(overrides)
    return watch


def ci_watch(**overrides) -> dict:
    watch = {"kind": "ci", "expected_source_sha": SHA,
             "project": "serveros", "major": True}
    watch.update(overrides)
    return watch


def verified_run(run_id: str = RUN, *, device: str = DEV,
                 session: str = SESS, delivery: str = DEL,
                 boot: str = BOOT) -> dict:
    return {"instanceId": INSTANCE, "deviceRef": device,
            "sessionRef": session, "runId": run_id,
            "deliveryId": delivery, "bootId": boot,
            "status": "completed",
            "lastAssistantMessage": {"content": "native output"}}


def running_run(run_id: str = RUN2, *, device: str = DEV2,
                session: str = SESS2, delivery: str = DEL2,
                boot: str = BOOT2) -> dict:
    return {"instanceId": INSTANCE, "deviceRef": device,
            "sessionRef": session, "runId": run_id,
            "deliveryId": delivery, "bootId": boot, "status": "running"}


def green_ci(*, head_sha: str = SHA, draft: bool = False,
             verified: bool = True) -> dict:
    """#493's offline-facts CI shape (its key set is closed)."""
    return {"head_sha": head_sha, "status": "completed",
            "conclusion": "success", "draft": draft, "verified": verified,
            "trusted": verified, "name": "ci", "id": "99"}


def ci_for_490(*, head_sha: str = SHA, draft: bool = False,
               verified: bool = True) -> dict:
    """#490's FixtureLegacyClient CI shape; it reads ``sourceSHA``/``isDraft``."""
    return {"sourceSHA": head_sha, "head_sha": head_sha,
            "conclusion": "success", "isDraft": draft, "verified": verified}


class CompositionTests(unittest.TestCase):
    def test_each_pinned_commit_supplied_the_module_it_is_imported_from(self):
        self.assertEqual(compose.PINNED, {
            493: "4f5320de7e187daadd79adc92e7487facd7b1820",
            488: "009662f36a2a5f8e11c51ae517834379a91a7770",
            490: "06a8be67422f1320e7bdfee38410f8b24d27b501",
            423: "d9b2948a62609e16007f2fe0ae288f9652d64cd1",
        })
        if os.environ.get("FLEET_QA_BASELINE_ROOT"):
            self.skipTest("baseline run: no PR tree to inspect")
        _assert_under(self, M["monitor"].__file__, CHECKOUTS[493])
        _assert_under(self, M["source_adapter"].__file__, CHECKOUTS[490])
        _assert_under(self, M["notify"].__file__, CHECKOUTS[423])
        _assert_under(self, M["activation"].__file__, CHECKOUTS[488])

    def test_no_status_word_means_done(self):
        contract = M["contract"]
        self.assertNotIn("DONE", contract.STATUSES)
        self.assertNotIn("SUCCESS", contract.STATUSES)
        self.assertEqual(contract.DISPATCHABLE,
                         frozenset({"TERMINAL_VERIFIED", "FAILED"}))

    def test_488_activation_resolves_the_zero_argument_tick(self):
        runtime = M["runtime"]
        entry = M["activation"].resolve_entry(
            "fleet_next_agent_monitor.runtime:tick")
        self.assertIs(entry, runtime.tick)
        saved = {}
        for key in list(os.environ):
            if key.startswith("FLEET_NEXT_AGENT_MONITOR_"):
                saved[key] = os.environ.pop(key)
        try:
            row = entry()
        finally:
            os.environ.update(saved)
        self.assertEqual(row["state"], "BLOCKED_INPUT")
        self.assertEqual(row["why"], "BLOCKED_RUNTIME_ENTRY")
        self.assertEqual(row["backlog"], 0)

    def test_the_batch_adapter_is_not_a_zero_argument_monitor_entry(self):
        entry = M["activation"].resolve_entry("batch_adapter:tick")
        with self.assertRaises(TypeError):
            entry()

        activation = M["activation"]
        act = activation.Activation(
            legacy_root=str(Path(tempfile.gettempdir()) / "qa-legacy"),
            next_root=str(Path(tempfile.gettempdir()) / "qa-next"),
            state=str(Path(tempfile.gettempdir()) / "qa-next" / "state.json"),
            lock=str(Path(tempfile.gettempdir()) / "qa-next" / "lock"),
            port=19101, legacy_port=19100,
            service_name="serveros-next-agent-monitor",
            legacy_service_name="serveros-legacy",
            plugin_id="serveros-next-monitor",
            legacy_plugin_id="serveros-legacy", ticks=1)
        result = activation.run_bounded(
            act, monitor_entry="batch_adapter:tick",
            legacy_read=activation.probe_true,
            next_write=activation.probe_false)
        self.assertEqual(result["state"], "TICK_FAILED")
        self.assertEqual(result["exit"], 2)
        self.assertEqual(result["ticks"], 1)

    def test_importing_the_monitor_does_not_import_the_batch_lane(self):
        code = (
            "import sys, fleet_next_agent_monitor.runtime;"
            "print('batch_adapter' in sys.modules,"
            "'serveros_fleet_v1_milestone_batch' in sys.modules)"
        )
        done = subprocess.run([sys.executable, "-c", code],
                              cwd=str(Path(tempfile.gettempdir())),
                              env={"PYTHONPATH": _composed_pythonpath(),
                                   "PATH": os.environ.get("PATH", "")},
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "False False")


class InterfaceDriftTests(unittest.TestCase):
    """Where two real modules disagree on a shape, pinned with exact values."""

    def test_490_and_493_disagree_about_what_native_activity_is(self):
        # #490 accepts transcript/messages; #493 accepts lastAssistantMessage.
        # The same CloudCLI answer is UNKNOWN in one adapter and VERIFIED in the
        # other. That is a contract drift, not a test artefact.
        grant = M["source_adapter"].CallerGrant.from_mapping({
            "principal": "qa", "devices": [DEV], "projects": ["serveros"],
            "sessions": [SESS], "views": ["run"]})
        fact = verified_run()
        client = M["source_adapter"].FixtureLegacyClient(
            {"run": fact}, fleet_instance_id=INSTANCE, route_id=ROUTE,
            boot_id=BOOT)
        adapter = M["source_adapter"].LegacyReadOnlySourceAdapter(client, grant)
        observation = adapter.observe_run(device_ref=DEV, session_ref=SESS,
                                          run_id=RUN)
        self.assertEqual(observation.status, "UNKNOWN")
        self.assertEqual(observation.reason, "NO_NATIVE_ACTIVITY")

        scenario = Scenario(self)
        scenario.write_facts(runs={RUN: fact})
        scenario.write_config([run_watch()], views=["run"])
        config = M["runtime"].load_config(json.loads(
            scenario.config_path.read_text(encoding="utf-8")))
        source = M["runtime"].build_source(
            config, offline_facts=scenario.facts_path)
        monitor_view = source.observe_run(device_ref=DEV, session_ref=SESS,
                                          run_id=RUN, delivery_id=DEL,
                                          expected_boot_id=BOOT)
        self.assertEqual(monitor_view["status"], "TERMINAL_VERIFIED")

    def test_490_reports_a_wrong_sha_as_identity_mismatch_not_sha_mismatch(self):
        # #493's contract says the reasons are byte-identical. They are not:
        # #490 has no SHA_MISMATCH word and reports IDENTITY_MISMATCH.
        grant = M["source_adapter"].CallerGrant.from_mapping({
            "principal": "qa", "devices": [], "projects": ["serveros"],
            "sessions": [], "views": ["ci"]})
        client = M["source_adapter"].FixtureLegacyClient(
            {"ci": ci_for_490(head_sha=OTHER_SHA)},
            fleet_instance_id=INSTANCE, route_id=ROUTE, boot_id=BOOT)
        adapter = M["source_adapter"].LegacyReadOnlySourceAdapter(client, grant)
        observation = adapter.observe_ci(source_sha=SHA, project="serveros")
        self.assertEqual((observation.status, observation.reason),
                         ("UNKNOWN", "IDENTITY_MISMATCH"))

        scenario = Scenario(self)
        scenario.write_facts(ci={SHA: green_ci(head_sha=OTHER_SHA)})
        scenario.write_config([ci_watch()], views=["ci"],
                              devices=[], sessions=[], projects=["serveros"])
        config = M["runtime"].load_config(json.loads(
            scenario.config_path.read_text(encoding="utf-8")))
        source = M["runtime"].build_source(
            config, offline_facts=scenario.facts_path)
        monitor_view = source.observe_ci(source_sha=SHA, project="serveros")
        self.assertEqual((monitor_view["status"], monitor_view["reason"]),
                         ("UNKNOWN", "SHA_MISMATCH"))
        self.assertNotIn("SHA_MISMATCH", dir(M["contract"]))
        self.assertIn("SHA_MISMATCH", M["contract"].REASONS)

    def test_batch_aggregator_refuses_423_head_without_send_text(self):
        # #488's Batch CLI needs MilestoneNotifier.send_text (batch.py:676-682).
        # #423's pinned head d9b2948 does not have it; #488's own tree carries a
        # 72-line later copy. This is the cross-PR dependency conflict.
        scenario = Scenario(self)
        notifier = M["notify"].MilestoneNotifier(
            M["qa_fakes"].JournalProvider(scenario.journal_path),
            scenario.ledger_path, conversation_ref="chat:qa")
        self.assertFalse(hasattr(notifier, "send_text"))

        path, filed = self._file_batch(scenario)
        with self.assertRaises(M["batch"].BatchRefused) as caught:
            M["batch"].BatchAggregator(filed.batch, notifier=notifier)
        self.assertEqual(caught.exception.code, "NOTIFIER_SEAM_MISSING")
        self.assertIn("send_text", caught.exception.why)
        self.assertTrue(path.exists())

    @staticmethod
    def _file_batch(scenario: Scenario):
        path = M["registration"].path_for(scenario.dir / "batches", "batch-qa-1")

        class Commander:
            def validate_batch(self, plan, snapshot):
                return {"verdict": "PASS", "batch_id": plan.get("batch_id"),
                        "task_count": len(plan.get("tasks") or []),
                        "blocked": []}

        plan = {"batch_id": "batch-qa-1", "tasks": [{
            "task_id": "t1", "device_ref": DEV, "runtime": "claude",
            "project_id": "serveros", "worktree": "/w/t1", "scope": "x",
            "acceptance": "a", "tests": "b", "report": "c",
            "idempotency_key": "idem-t1", "session_ref": SESS}]}
        acks = [{"task_id": "t1", "device_ref": DEV, "run_id": RUN,
                 "delivery_id": DEL, "boot_id": BOOT}]
        filed, outcome = M["registration"].from_commander(
            plan, acks, contract=Commander(), snapshot={"devices": {}},
            source="cloudcli", registered_at=_iso(3600),
            conversation_ref="chat:qa", expires_at="2099-12-31T00:00:00Z",
            path=path)
        if outcome != M["registration"].REGISTERED:
            raise AssertionError(f"batch was not filed: {outcome}")
        return path, filed


class MonitorNotifierIntegrationTests(unittest.TestCase):
    def test_a_verified_run_is_delivered_once_through_423(self):
        scenario = Scenario(self)
        scenario.write_facts(runs={RUN: verified_run()})
        scenario.write_config([run_watch()], views=["run"])

        os.environ.update({
            "QA_JOURNAL": str(scenario.journal_path),
            "QA_LEDGER": str(scenario.ledger_path),
        })
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in
                                 ("QA_JOURNAL", "QA_LEDGER")])
        document = json.loads(scenario.config_path.read_text(encoding="utf-8"))
        core, _config, blocked = M["runtime"].build_monitor(
            document, state_path=scenario.state_path,
            notifier_spec="qa_fakes:notifier_factory",
            offline_facts=scenario.facts_path)
        self.assertIsNone(blocked)
        tick = core.run_once()
        self.assertEqual(tick["state"], "SETTLED")
        self.assertEqual(tick["exit"], 0)
        self.assertEqual(tick["delivered"], 1)
        self.assertEqual(tick["unknown_receipts"], 0)
        self.assertEqual(len(scenario.journal()), 1)
        rows = scenario.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "DELIVERED")
        self.assertEqual(
            rows[0]["delivery_key"],
            f"milestone:cloudcli:{DEV}:RUN_COMPLETED:{RUN}:VERIFIED_GREEN")
        self.assertTrue(rows[0]["transport_key"].startswith("ms1:"))
        self.assertEqual(len(rows[0]["transport_key"]), 50)

    def test_unknown_is_not_upgraded_to_verified_across_processes(self):
        scenario = Scenario(self)
        no_output = dict(verified_run())
        no_output.pop("lastAssistantMessage")
        scenario.write_facts(runs={RUN: no_output})
        scenario.write_config([run_watch()], views=["run"])

        for index in range(3):
            code, tick, done = scenario.run_monitor()
            self.assertEqual(code, 0, done.stderr)
            self.assertEqual(tick["counts"], {"UNKNOWN": 1})
            self.assertEqual(tick["dispatched"], 0)
            self.assertEqual(tick["delivered"], 0)
            self.assertEqual(tick["watches"][0]["status"], "UNKNOWN")
            self.assertEqual(tick["watches"][0]["reason"],
                             "NO_NATIVE_ACTIVITY")
            self.assertEqual(scenario.journal(), [])
            self.assertEqual(scenario.ledger_rows(), [])
            self.assertEqual(scenario.state_document()["cursor"], {},
                             f"tick {index + 1} recorded a non-delivery")

        scenario.write_facts(runs={RUN: verified_run()})
        code, tick, done = scenario.run_monitor()
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(tick["counts"], {"TERMINAL_VERIFIED": 1})
        self.assertEqual(tick["delivered"], 1)
        self.assertEqual(len(scenario.journal()), 1)

    def test_watch_level_run_delivery_boot_and_session_reach_the_payload_apart(self):
        """The #423 envelope is built from the watch, not from the other watch.

        This is the payload half of identity separation; the subprocess test
        above is the durable-cursor half. The notifier here is a capture seam so
        the payload can be read directly; #423 itself is exercised by the
        delivery tests.
        """
        scenario = Scenario(self)
        scenario.write_facts(runs={RUN: verified_run(),
                                   RUN2: verified_run(
                                       run_id=RUN2, device=DEV2, session=SESS2,
                                       delivery=DEL2, boot=BOOT2)})
        scenario.write_config([
            run_watch(),
            run_watch(device_ref=DEV2, session_ref=SESS2, run_id=RUN2,
                      delivery_id=DEL2, expected_boot_id=BOOT2),
        ], views=["run"])

        class Capture:
            def __init__(self):
                self.calls = []

            def observe_payload(self, source, payload):
                self.calls.append((source, payload))
                return {"state": "DELIVERED", "delivered": True,
                        "remote_id": f"om_capture_{len(self.calls)}"}

        config = M["runtime"].load_config(json.loads(
            scenario.config_path.read_text(encoding="utf-8")))
        source = M["runtime"].build_source(
            config, offline_facts=scenario.facts_path)
        capture = Capture()
        core = M["core"](
            source=source, notifier=capture,
            state=M["state"](scenario.state_path),
            watches=config["watches"], debounce_ticks=1)
        tick = core.run_once()
        self.assertEqual(tick["dispatched"], 2)
        self.assertEqual(tick["delivered"], 2)

        by_run = {payload["run"]["runId"]: payload
                  for _source, payload in capture.calls}
        self.assertEqual(set(by_run), {RUN, RUN2})
        self.assertEqual(by_run[RUN]["run"]["bootId"], BOOT)
        self.assertEqual(by_run[RUN]["run"]["sessionId"], SESS)
        self.assertEqual(by_run[RUN2]["run"]["bootId"], BOOT2)
        self.assertEqual(by_run[RUN2]["run"]["sessionId"], SESS2)
        self.assertEqual(by_run[RUN]["device_ref"], DEV)
        self.assertEqual(by_run[RUN2]["device_ref"], DEV2)
        self.assertNotIn(RUN2, json.dumps(by_run[RUN]))
        self.assertNotIn(RUN, json.dumps(by_run[RUN2]))
        self.assertNotIn(BOOT2, json.dumps(by_run[RUN]))
        self.assertNotIn(BOOT, json.dumps(by_run[RUN2]))

    def test_lost_expired_offline_draft_wrong_sha_unverified_and_denied_are_not_success(self):
        cases = [
            ("lost", {"runs": {}, "views": ["run"], "watch": run_watch()},
             "UNKNOWN", "NEVER_ISSUED"),
            ("expired", {"runs": {RUN: {"raise": "EXPIRED"}},
                         "views": ["run"], "watch": run_watch()},
             "UNKNOWN", "EXPIRED"),
            ("offline", {"runs": {RUN: {"raise": "OFFLINE"}},
                         "views": ["run"], "watch": run_watch()},
             "OFFLINE", "ROUTE_OFFLINE"),
            ("network", {"runs": {RUN: {"raise": "NETWORK"}},
                         "views": ["run"], "watch": run_watch()},
             "OFFLINE", "ROUTE_OFFLINE"),
            ("ci-draft", {"ci": {SHA: green_ci(draft=True)}, "views": ["ci"],
                          "watch": ci_watch()}, "UNKNOWN", "DRAFT_CI"),
            ("ci-wrong-sha", {"ci": {SHA: green_ci(head_sha=OTHER_SHA)},
                              "views": ["ci"], "watch": ci_watch()},
             "UNKNOWN", "SHA_MISMATCH"),
            ("ci-unverified", {"ci": {SHA: green_ci(verified=False)},
                               "views": ["ci"], "watch": ci_watch()},
             "UNKNOWN", "UNVERIFIED_CI"),
            ("denied", {"runs": {RUN: verified_run()}, "views": ["run"],
                        "watch": run_watch(), "devices": ["device:other"]},
             "DENIED", "NOT_GRANTED"),
        ]
        for name, case, status, reason in cases:
            with self.subTest(name):
                scenario = Scenario(self)
                scenario.write_facts(runs=case.get("runs"),
                                     ci=case.get("ci"))
                scenario.write_config(
                    [case["watch"]], views=case["views"],
                    devices=case.get("devices",
                                     [DEV, DEV2] if case["views"] == ["run"]
                                     else []),
                    sessions=[SESS, SESS2] if case["views"] == ["run"] else [],
                    projects=["serveros"] if case["views"] == ["ci"] else [])
                code, tick, done = scenario.run_monitor()
                self.assertIn(code, (0, 4), done.stderr)
                self.assertEqual(tick["dispatched"], 0)
                self.assertEqual(tick["delivered"], 0)
                self.assertEqual(tick["unknown_receipts"], 0)
                self.assertEqual(tick["watches"][0]["status"], status)
                if reason is not None:
                    self.assertEqual(tick["watches"][0]["reason"], reason)
                self.assertNotEqual(tick["watches"][0]["status"],
                                    "TERMINAL_VERIFIED")
                self.assertEqual(scenario.journal(), [])
                self.assertEqual(scenario.ledger_rows(), [])

    def test_delivery_unknown_becomes_settled_on_the_next_tick(self):
        """Characterisation: the false success this QA found.

        Tick 1 correctly says UNCONFIRMED. Tick 2 reads the pre-written cursor
        as ``already`` and calls it ``DUPLICATE`` — with the ledger still at
        ``DELIVERY_UNKNOWN`` and the provider never having confirmed. The state
        is then SETTLED. This test pins the defect; the next one states the
        behaviour owed.
        """
        scenario = Scenario(self)
        scenario.write_facts(runs={RUN: verified_run()})
        scenario.write_config([run_watch()], views=["run"])
        mode = {"QA_PROVIDER_MODE": "unknown"}

        code1, tick1, done1 = scenario.run_monitor(extra_env=mode)
        self.assertEqual(code1, 4, done1.stderr)
        self.assertEqual(tick1["state"], "UNCONFIRMED")
        self.assertEqual(tick1["unknown_receipts"], 1)
        self.assertEqual(tick1["delivered"], 0)
        self.assertEqual(len(scenario.journal()), 1)

        code2, tick2, done2 = scenario.run_monitor(extra_env=mode)
        self.assertEqual(code2, 0, done2.stderr)
        self.assertEqual(tick2["state"], "SETTLED")
        self.assertEqual(tick2["duplicate"], 1)
        self.assertEqual(tick2["delivered"], 0)
        self.assertEqual(tick2["unknown_receipts"], 0)
        rows = scenario.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "DELIVERY_UNKNOWN")
        self.assertIsNone(rows[0]["remote_id"])
        self.assertEqual(len(scenario.journal()), 1)

    @unittest.expectedFailure
    def test_an_unconfirmed_delivery_must_stay_unconfirmed(self):
        scenario = Scenario(self)
        scenario.write_facts(runs={RUN: verified_run()})
        scenario.write_config([run_watch()], views=["run"])
        mode = {"QA_PROVIDER_MODE": "unknown"}
        scenario.run_monitor(extra_env=mode)
        _code, tick, _done = scenario.run_monitor(extra_env=mode)
        self.assertEqual(tick["state"], "UNCONFIRMED")
        self.assertEqual(tick["unknown_receipts"], 1)
        self.assertEqual(tick["delivered"], 0)

    def test_ci_is_verified_by_the_monitor_but_cannot_reach_423(self):
        """Characterisation of the empty ``device_ref`` in #493's GH payload."""
        scenario = Scenario(self)
        scenario.write_facts(ci={SHA: green_ci()})
        scenario.write_config([ci_watch()], views=["ci"],
                              devices=[], sessions=[], projects=["serveros"])
        code, tick, done = scenario.run_monitor()
        self.assertEqual(code, 4, done.stderr)
        self.assertEqual(tick["counts"], {"TERMINAL_VERIFIED": 1})
        self.assertEqual(tick["dispatched"], 1)
        self.assertEqual(tick["delivered"], 0)
        self.assertEqual(tick["unknown_receipts"], 1)
        receipt = tick["watches"][0]
        self.assertEqual(receipt["receipt"], "DELIVERY_UNKNOWN")
        self.assertIn("device_ref is required", receipt["receipt_why"])
        self.assertEqual(scenario.journal(), [])
        self.assertEqual(scenario.ledger_rows(), [])

    @unittest.expectedFailure
    def test_a_verified_ci_fact_must_reach_the_one_notifier(self):
        scenario = Scenario(self)
        scenario.write_facts(ci={SHA: green_ci()})
        scenario.write_config([ci_watch()], views=["ci"],
                              devices=[], sessions=[], projects=["serveros"])
        _code, tick, _done = scenario.run_monitor()
        self.assertEqual(tick["delivered"], 1)
        self.assertEqual(tick["unknown_receipts"], 0)


class CrossProcessTests(unittest.TestCase):
    def test_three_activation_processes_deliver_once_and_keep_identities_apart(self):
        scenario = Scenario(self)
        scenario.write_facts(runs={RUN: verified_run()})
        scenario.write_config([run_watch()], views=["run"])

        epochs: list[str] = []
        recovered: list[str | None] = []
        for _ in range(3):
            code, result, done = scenario.run_activation()
            self.assertEqual(code, 0, done.stderr)
            self.assertEqual(result["state"], "BOUNDED_STOP")
            state = scenario.state_document()
            epochs.append(state["boot_epoch"])
            recovered.append(state.get("recovered_from"))
            self.assertIsNotNone(state["last_tick_at"])
            self.assertIsNotNone(state["last_ok_at"])

        self.assertEqual(len(set(epochs)), 3, epochs)
        self.assertEqual(recovered[0], None)
        self.assertEqual(recovered[1], epochs[0])
        self.assertEqual(recovered[2], epochs[1])

        journal = scenario.journal()
        self.assertEqual(len(journal), 1, journal)
        self.assertIn(f"run:{RUN}", journal[0]["text"])
        self.assertNotIn(SHA, journal[0]["text"])
        self.assertTrue(journal[0]["idempotency_key"].startswith("ms1:"))
        self.assertEqual(len(journal[0]["idempotency_key"]), 50)

        rows = scenario.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["delivery_key"],
            f"milestone:cloudcli:{DEV}:RUN_COMPLETED:{RUN}:VERIFIED_GREEN")
        self.assertEqual(rows[0]["state"], "DELIVERED")

        state = scenario.state_document()
        self.assertEqual(len(state["cursor"]), 1)
        key, cursor = next(iter(state["cursor"].items()))
        self.assertEqual(key, f"run|{DEV}|{SESS}|{RUN}")
        self.assertIn(RUN, cursor["fingerprint"])
        self.assertIn(DEL, cursor["fingerprint"])
        self.assertIn(BOOT, cursor["fingerprint"])
        self.assertNotIn(SHA, cursor["fingerprint"])

    def test_a_lost_cursor_does_not_resend_because_the_423_ledger_remembers(self):
        scenario = Scenario(self)
        scenario.write_facts(runs={RUN: verified_run()})
        scenario.write_config([run_watch()], views=["run"])
        for _ in range(3):
            code, _result, done = scenario.run_activation()
            self.assertEqual(code, 0, done.stderr)
        self.assertEqual(len(scenario.journal()), 1)

        scenario.state_path.unlink()
        if scenario.lock_path.exists():
            scenario.lock_path.unlink()

        code, tick, done = scenario.run_monitor()
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(tick["state"], "SETTLED")
        self.assertEqual(tick["duplicate"], 1)
        self.assertEqual(tick["delivered"], 0)
        self.assertEqual(tick["dispatched"], 1)
        self.assertEqual(len(scenario.journal()), 1,
                         "the 423 ledger did not stop the replay")
        self.assertEqual(len(scenario.ledger_rows()), 1)

    def test_two_runs_keep_run_delivery_boot_and_sha_separate(self):
        scenario = Scenario(self)
        scenario.write_facts(runs={RUN: verified_run(),
                                   RUN2: verified_run(
                                       run_id=RUN2, device=DEV2, session=SESS2,
                                       delivery=DEL2, boot=BOOT2)})
        scenario.write_config([
            run_watch(),
            run_watch(device_ref=DEV2, session_ref=SESS2, run_id=RUN2,
                      delivery_id=DEL2, expected_boot_id=BOOT2),
        ], views=["run"])

        code, tick, done = scenario.run_monitor()
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(tick["delivered"], 2)

        journal = scenario.journal()
        self.assertEqual(len(journal), 2)
        first = next(row for row in journal if RUN in row["text"])
        second = next(row for row in journal if RUN2 in row["text"])
        self.assertNotIn(RUN2, first["text"])
        self.assertNotIn(RUN, second["text"])

        state = scenario.state_document()
        self.assertEqual(set(state["cursor"]), {
            f"run|{DEV}|{SESS}|{RUN}",
            f"run|{DEV2}|{SESS2}|{RUN2}",
        })
        first_cursor = state["cursor"][f"run|{DEV}|{SESS}|{RUN}"]
        second_cursor = state["cursor"][f"run|{DEV2}|{SESS2}|{RUN2}"]
        self.assertIn(DEL, first_cursor["fingerprint"])
        self.assertNotIn(DEL2, first_cursor["fingerprint"])
        self.assertIn(DEL2, second_cursor["fingerprint"])
        self.assertNotIn(DEL, second_cursor["fingerprint"])
        # The durable fingerprint carries the *client's* declared boot id, not
        # the watch's expected_boot_id (sources.py:237 vs sources.py:354). A
        # watch-level boot expectation therefore does not separate two facts in
        # the cursor; the watch key does. Pinned here as an observed gap.
        self.assertIn(BOOT, first_cursor["fingerprint"])
        self.assertIn(BOOT, second_cursor["fingerprint"])
        self.assertNotIn(BOOT2, second_cursor["fingerprint"])

        rows = scenario.ledger_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row["delivery_key"] for row in rows}), 2)
        self.assertTrue(all(row["state"] == "DELIVERED" for row in rows))

    def test_batch_and_monitor_share_one_ledger_across_two_real_processes(self):
        """#481's Batch CLI sends first; the monitor must read DUPLICATE.

        The Batch CLI loads its own copy of #423 (the one with ``send_text``);
        the monitor loads #423's pinned head. The *ledger schema and delivery
        key* are the shared contract, and this test is what proves they still
        agree after the two revisions drifted.
        """
        scenario = Scenario(self)
        path, _filed = InterfaceDriftTests._file_batch(scenario)

        batch_env = scenario.env(extra={
            "QA_PROVIDER_MODE": "deliver",
            "QA_JOURNAL": str(scenario.journal_path),
        })
        batch_args = [
            sys.executable,
            str(CHECKOUTS[488] / compose.BATCH_DIR / "cli.py"),
            "--registration", str(path),
            "--ledger", str(scenario.ledger_path),
            "--state", str(scenario.batch_state),
            "--lock", str(scenario.batch_lock),
            "--provider-factory", "qa_fakes:batch_provider",
            "--source-factory", "qa_fakes:batch_source",
        ]
        batch = subprocess.run(batch_args, cwd=str(scenario.dir), env=batch_env,
                               capture_output=True, text=True, timeout=120)
        self.assertEqual(batch.returncode, 0, batch.stderr)
        batch_document = json.loads(batch.stdout)
        self.assertEqual(batch_document["state"], "SETTLED")
        self.assertEqual(batch_document["summary"]["state"], "DELIVERED")
        self.assertEqual(len(scenario.journal()), 2,
                         "expected one member card and one batch summary")

        scenario.write_facts(runs={RUN: verified_run()})
        scenario.write_config([run_watch()], views=["run"])
        code, tick, done = scenario.run_monitor()
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(tick["state"], "SETTLED")
        self.assertEqual(tick["duplicate"], 1)
        self.assertEqual(tick["delivered"], 0)
        self.assertEqual(len(scenario.journal()), 2,
                         "the monitor sent a second summary for the same fact")
        self.assertEqual(len(scenario.ledger_rows()), 2)

    def test_background_silence_reports_heartbeat_backlog_and_stale(self):
        scenario = Scenario(self)
        scenario.write_facts(runs={RUN: verified_run(), RUN2: running_run()})
        scenario.write_config([run_watch(), run_watch(
            device_ref=DEV2, session_ref=SESS2, run_id=RUN2,
            delivery_id=DEL2, expected_boot_id=BOOT2)], views=["run"])

        old = _iso(7200)
        scenario.state_path.write_text(json.dumps({
            "schema": STATE_SCHEMA, "boot_epoch": "old-epoch", "writer_pid": 1,
            "last_tick_at": old, "last_ok_at": old,
            "consecutive_failures": 0, "cursor": {}, "alerts": {},
            "recovered_from": None, "_first_seen_at": 0,
        }), encoding="utf-8")

        config = M["runtime"].load_config(json.loads(
            scenario.config_path.read_text(encoding="utf-8")))
        source = M["runtime"].build_source(
            config, offline_facts=scenario.facts_path)
        core = M["core"](
            source=source, notifier=None,
            state=M["state"](scenario.state_path),
            watches=config["watches"], debounce_ticks=1, stale_after=3600)
        tick = core.run_once()

        self.assertEqual(tick["state"], "BLOCKED_INPUT")
        self.assertEqual(tick["exit"], 3)
        self.assertEqual(tick["backlog"], 1)
        self.assertTrue(tick["health"]["stale"])
        self.assertGreater(tick["health"]["cursor_age"], 3600)
        self.assertEqual(tick["health"]["consecutive_failures"], 1)
        self.assertIsNotNone(tick["health"]["last_tick"])
        self.assertEqual(tick["health"]["last_ok"], old)
        codes = {warning["code"] for warning in tick["warnings"]}
        self.assertIn("STALE_MONITOR", codes)
        self.assertIn("BACKLOG", codes)
        self.assertIn("BLOCKED_LEDGER", codes)
        self.assertIn("CONSECUTIVE_FAILURES", codes)


class ActivationReportingTests(unittest.TestCase):
    def test_a_blocked_tick_is_reported_as_bounded_stop(self):
        """Characterisation: the supervisor cannot see the monitor's refusal."""
        scenario = Scenario(self)
        code, result, done = scenario.run_activation(configured=False)
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(result["state"], "BOUNDED_STOP")
        self.assertEqual(result["exit"], 0)
        self.assertEqual(result["health"]["last_tick"], 0.0)
        self.assertFalse(result["health"]["stale"])
        self.assertEqual(result["warnings"], [])

    @unittest.expectedFailure
    def test_activation_must_not_report_a_blocked_tick_as_success(self):
        scenario = Scenario(self)
        _code, result, _done = scenario.run_activation(configured=False)
        self.assertEqual(result["state"], "BLOCKED_INPUT")
        self.assertEqual(result["exit"], 3)

    def test_activation_can_compute_stale_but_its_cli_never_passes_the_age(self):
        activation = M["activation"]
        scenario = Scenario(self)
        act = activation.Activation(
            legacy_root=str(scenario.legacy), next_root=str(scenario.next),
            state=str(scenario.state_path), lock=str(scenario.lock_path),
            port=19101, legacy_port=19100,
            service_name="serveros-next-agent-monitor",
            legacy_service_name="serveros-legacy",
            plugin_id="serveros-next-monitor",
            legacy_plugin_id="serveros-legacy", ticks=1)
        result = activation.run_bounded(
            act, monitor_entry=lambda: {"backlog": 0, "unknown_receipts": 0},
            legacy_read=activation.probe_true,
            next_write=activation.probe_false,
            cursor_age_seconds=7200.0)
        self.assertTrue(result["health"]["stale"])
        self.assertEqual([w["code"] for w in result["warnings"]],
                         ["STALE_MONITOR"])

        scenario.write_facts(runs={RUN2: running_run()})
        scenario.write_config([run_watch(
            device_ref=DEV2, session_ref=SESS2, run_id=RUN2,
            delivery_id=DEL2, expected_boot_id=BOOT2)], views=["run"])
        code, cli_result, done = scenario.run_activation()
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(cli_result["health"]["backlog"], 1)
        self.assertEqual(cli_result["health"]["last_tick"], 0.0)
        self.assertFalse(cli_result["health"]["stale"])


class SourceAdapterBridgeTests(unittest.TestCase):
    def _adapter(self, document: dict, *, boot_id: str = BOOT):
        grant = M["source_adapter"].CallerGrant.from_mapping({
            "principal": "qa", "devices": [DEV], "projects": ["serveros"],
            "sessions": [SESS], "views": ["run", "ci"]})
        client = M["source_adapter"].FixtureLegacyClient(
            document, fleet_instance_id=INSTANCE, route_id=ROUTE,
            boot_id=boot_id)
        return M["source_adapter"].LegacyReadOnlySourceAdapter(client, grant)

    def test_490_observation_reaches_493_core_and_423(self):
        # #490's native-evidence vocabulary is transcript/messages, so this
        # fixture carries a transcript. The real CloudCLI shape
        # (lastAssistantMessage) is the drift pinned in InterfaceDriftTests.
        fact = dict(verified_run())
        fact.pop("lastAssistantMessage")
        fact["transcript"] = [{"role": "assistant", "content": "native output"}]
        adapter = self._adapter({"run": fact})
        observation = adapter.observe_run(device_ref=DEV, session_ref=SESS,
                                          run_id=RUN, project="serveros")
        self.assertEqual(observation.status, "TERMINAL_VERIFIED")
        self.assertEqual(adapter.readings_used, 1)

        scenario = Scenario(self)
        notifier = M["notify"].MilestoneNotifier(
            M["qa_fakes"].JournalProvider(scenario.journal_path),
            scenario.ledger_path, conversation_ref="chat:qa")
        core_adapter = self._adapter({"run": fact})
        core = M["core"](
            source=M["source_bridge"].BridgedLegacySource(core_adapter),
            notifier=notifier,
            state=M["state"](scenario.state_path),
            watches=[run_watch()], debounce_ticks=1)
        tick = core.run_once()
        self.assertEqual(tick["state"], "SETTLED")
        self.assertEqual(tick["delivered"], 1)
        self.assertEqual(tick["reads"], 1)
        self.assertEqual(core_adapter.readings_used, 1)
        self.assertEqual(len(scenario.journal()), 1)

    def test_490_boot_mismatch_stays_unknown_and_is_not_dispatched(self):
        fact = dict(verified_run())
        fact["bootId"] = "boot-other"
        fact.pop("lastAssistantMessage")
        fact["transcript"] = [{"role": "assistant", "content": "native output"}]
        adapter = self._adapter({"run": fact}, boot_id=BOOT)
        observation = adapter.observe_run(device_ref=DEV, session_ref=SESS,
                                          run_id=RUN, project="serveros")
        self.assertEqual((observation.status, observation.reason),
                         ("UNKNOWN", "BOOT_MISMATCH"))

        scenario = Scenario(self)
        notifier = M["notify"].MilestoneNotifier(
            M["qa_fakes"].JournalProvider(scenario.journal_path),
            scenario.ledger_path, conversation_ref="chat:qa")
        core = M["core"](
            source=M["source_bridge"].BridgedLegacySource(adapter),
            notifier=notifier,
            state=M["state"](scenario.state_path),
            watches=[run_watch()], debounce_ticks=1)
        tick = core.run_once()
        self.assertEqual(tick["dispatched"], 0)
        self.assertEqual(scenario.journal(), [])
        self.assertEqual(scenario.ledger_rows(), [])

    def test_490_cannot_be_substituted_without_a_bridge(self):
        adapter = self._adapter({"run": verified_run()})
        with self.assertRaises(TypeError) as caught:
            M["observe_watch"](adapter, run_watch())
        self.assertIn("expected_boot_id", str(caught.exception))
        self.assertIn("expected_boot_id",
                      M["source_bridge"].DIRECT_SUBSTITUTION)

    @unittest.expectedFailure
    def test_490_adapter_should_plug_into_493_unchanged(self):
        adapter = self._adapter({"run": verified_run()})
        view = M["observe_watch"](adapter, run_watch())
        self.assertEqual(view["status"], "TERMINAL_VERIFIED")

    def test_490_denial_is_an_exception_not_a_denied_observation(self):
        grant = M["source_adapter"].CallerGrant.from_mapping({
            "principal": "qa", "devices": ["device:other"], "projects": [],
            "sessions": [], "views": ["run"]})
        client = M["source_adapter"].FixtureLegacyClient(
            {"run": verified_run()}, fleet_instance_id=INSTANCE,
            route_id=ROUTE, boot_id=BOOT)
        adapter = M["source_adapter"].LegacyReadOnlySourceAdapter(client, grant)
        with self.assertRaises(M["source_adapter"].SourceRefused) as caught:
            adapter.observe_run(device_ref=DEV, session_ref=SESS, run_id=RUN)
        self.assertEqual(caught.exception.code, "DENIED")
        self.assertEqual(client.calls, [], "a denial must cost zero reads")

    @unittest.expectedFailure
    def test_490_denial_should_be_a_denied_observation_for_493(self):
        grant = M["source_adapter"].CallerGrant.from_mapping({
            "principal": "qa", "devices": ["device:other"], "projects": [],
            "sessions": [], "views": ["run"]})
        client = M["source_adapter"].FixtureLegacyClient(
            {"run": verified_run()}, fleet_instance_id=INSTANCE,
            route_id=ROUTE, boot_id=BOOT)
        adapter = M["source_adapter"].LegacyReadOnlySourceAdapter(client, grant)
        view = M["observe_watch"](
            M["source_bridge"].BridgedLegacySource(adapter), run_watch())
        self.assertEqual(view["status"], "DENIED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
