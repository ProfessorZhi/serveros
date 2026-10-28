#!/usr/bin/env python3
"""Wave 16 independent cross-PR QA: #494 + #497 + #498 + #499.

The code under test is the **real git merge** of the four commits in
``integrations/fleet-next-wave16-crosspr-qa/compose.py``. The fix files are
checked against their author-head blob ids, so a test cannot pass against a
different revision with the same function names.

Layers
------
* CODE / UNIT / CI are exercised here. Every provider is a file-backed fake;
  no socket, credential, model or Feishu call is made.
* REAL_DEVICE / PRODUCTION are **NOT_RUN**. No timer, systemd unit or live
  Legacy client is touched.

P0 assertions are ordinary tests where the merged fix works. The two defects
that survive the merge are pinned as a passing characterisation plus an
``expectedFailure`` stating the owed behaviour; the count is not reduced by
hand. The RED baseline is produced by ``baseline_probe.py`` against #494.
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
QA_DIR = REPO / "integrations" / "fleet-next-wave16-crosspr-qa"
if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))

import compose  # noqa: E402

DEV = "device:tokyo-qa-1"
DEV2 = "device:tokyo-qa-2"
VICTIM = "device:victim"
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

M: dict[str, object] = {}
TREE: Path | None = None
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
    global TREE, ADDED_PATHS
    TREE = compose.tree_root()
    missing = []
    for (pr, relative), expected in compose.FIX_BLOBS.items():
        path = TREE / relative
        if not path.is_file() or compose.git_blob_sha(path.read_bytes()) != expected:
            missing.append(f"{pr}:{relative}")
    if missing:
        raise RuntimeError(
            "BASELINE_RED: the Wave 16 fix blobs are absent or different "
            f"from {sorted(missing)}; run baseline_probe.py for the #494 "
            "before-picture instead of this suite")
    ADDED_PATHS = compose.install(TREE)

    import fleet_next_agent_monitor as monitor
    import fleet_next_agent_monitor.runtime as monitor_runtime
    import fleet_next_agent_monitor.contract as monitor_contract
    import milestone_notify
    import activation
    import fleet_next_monitor_source_adapter as source_adapter
    import fleet_next_monitor_source_adapter.compat as compat
    import qa_fakes

    from fleet_next_agent_monitor.core import MonitorCore
    from fleet_next_agent_monitor.state import DurableState
    from fleet_next_agent_monitor.sources import observe_watch

    batch = _load("serveros_fleet_v1_milestone_batch",
                  TREE / compose.BATCH_DIR / "batch.py")
    registration = _load(
        "serveros_fleet_v1_milestone_batch_registration",
        TREE / compose.BATCH_DIR / "registration.py")

    M.update({
        "monitor": monitor,
        "runtime": monitor_runtime,
        "contract": monitor_contract,
        "core": MonitorCore,
        "state": DurableState,
        "observe_watch": observe_watch,
        "notify": milestone_notify,
        "activation": activation,
        "source_adapter": source_adapter,
        "compat": compat,
        "qa_fakes": qa_fakes,
        "batch": batch,
        "registration": registration,
    })


def tearDownModule() -> None:  # noqa: N802 - unittest protocol
    for path in ADDED_PATHS:
        try:
            sys.path.remove(path)
        except ValueError:
            pass


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("FLEET_NEXT_AGENT_MONITOR_"):
            env.pop(key)
    env.pop("FLEET_QA_BASELINE_ROOT", None)
    return env


def _pythonpath() -> str:
    return os.pathsep.join([str(QA_DIR), *compose.source_paths(TREE)])


def _iso(seconds_ago: float = 0.0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(time.time() - seconds_ago))


class Scenario:
    def __init__(self, test: unittest.TestCase) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="wave16-scenario-"))
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

    def write_facts(self, *, runs: dict | None = None,
                    ci: dict | None = None) -> None:
        self.facts_path.write_text(json.dumps({
            "fleet_instance_id": INSTANCE, "route_id": ROUTE,
            "boot_id": BOOT, "runs": runs or {}, "ci": ci or {},
        }), encoding="utf-8")

    def write_config(self, watches: list[dict], *, views: list[str],
                     devices: list[str] | None = None,
                     sessions: list[str] | None = None,
                     projects: list[str] | None = None,
                     bounds: dict | None = None) -> None:
        self.config_path.write_text(json.dumps({
            "schema": "serveros/fleet-next-agent-monitor-config.v1",
            "grant": {
                "principal": "qa-wave16",
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

    def env(self, *, notifier: bool = True,
            extra: dict | None = None) -> dict[str, str]:
        env = _clean_env()
        env["PYTHONPATH"] = _pythonpath()
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
            env["FLEET_NEXT_AGENT_MONITOR_NOTIFIER"] = "qa_fakes:notifier_factory"
        if extra:
            env.update(extra)
        return env

    def run_monitor(self, *, notifier: bool = True,
                    extra_env: dict | None = None,
                    timeout: float = 120.0) -> tuple[int, dict, subprocess.CompletedProcess]:
        args = [sys.executable, "-m", "fleet_next_agent_monitor",
                "--config", str(self.config_path),
                "--state", str(self.state_path),
                "--offline-facts", str(self.facts_path)]
        if notifier:
            args += ["--notifier-entry", "qa_fakes:notifier_factory"]
        done = subprocess.run(args, cwd=str(self.dir),
                              env=self.env(notifier=notifier, extra=extra_env),
                              capture_output=True, text=True, timeout=timeout)
        document = json.loads(done.stdout) if done.stdout.strip() else {}
        return done.returncode, document, done

    def run_activation(self, *, configured: bool = True, ticks: int = 1,
                       cursor_age_seconds: float | None = None,
                       monitor_entry: str = "fleet_next_agent_monitor.runtime:tick",
                       extra_env: dict | None = None,
                       timeout: float = 120.0) -> tuple[int, dict, subprocess.CompletedProcess]:
        roots = [str(QA_DIR), *compose.source_paths(TREE)]
        args = [
            sys.executable, str(QA_DIR / "run_activation.py"),
            "--legacy-root", str(self.legacy),
            "--next-root", str(self.next),
            "--state", str(self.state_path),
            "--lock", str(self.lock_path),
            "--port", "19301", "--legacy-port", "19300",
            "--service-name", "serveros-next-agent-monitor",
            "--legacy-service-name", "serveros-legacy",
            "--plugin-id", "serveros-next-monitor",
            "--legacy-plugin-id", "serveros-legacy",
            "--ticks", str(ticks),
            "--monitor-entry", monitor_entry,
            "--legacy-read", "activation:probe_true",
            "--next-write", "activation:probe_false",
        ]
        if cursor_age_seconds is not None:
            args += ["--cursor-age-seconds", str(cursor_age_seconds)]
        env = _clean_env()
        env["PYTHONPATH"] = _pythonpath()
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
                 boot: str = BOOT, message: str | None = "native output",
                 note: str | None = None) -> dict:
    fact = {"instanceId": INSTANCE, "deviceRef": device,
            "sessionRef": session, "runId": run_id,
            "deliveryId": delivery, "bootId": boot,
            "status": "completed"}
    if message is not None:
        fact["lastAssistantMessage"] = {"content": message}
    if note is not None:
        fact["note"] = note
    return fact


def green_ci(*, head_sha: str = SHA, draft: bool = False,
             verified: bool = True, status: str = "completed") -> dict:
    return {"head_sha": head_sha, "status": status,
            "conclusion": "success" if status == "completed" else None,
            "draft": draft, "verified": verified, "trusted": verified,
            "name": "ci", "id": "99"}


def ci_for_499(*, head_sha: str = SHA, draft: bool = False,
               verified: bool = True, status: str = "completed") -> dict:
    return {"sourceSHA": head_sha, "head_sha": head_sha,
            "status": status, "conclusion": "success" if status == "completed" else None,
            "isDraft": draft, "verified": verified}


class CompositionTests(unittest.TestCase):
    def test_the_three_fix_files_are_exactly_the_author_head_blobs(self):
        for (pr, relative), expected in compose.FIX_BLOBS.items():
            with self.subTest(pr=pr, path=relative):
                path = TREE / relative
                self.assertTrue(path.is_file(), path)
                self.assertEqual(compose.git_blob_sha(path.read_bytes()), expected)
        notifier = (TREE / compose.INTEGRATION_DIRS[423]
                    / "milestone_notify.py").read_bytes()
        self.assertIn(b"def send_text", notifier)

    def test_baseline_494_reproduces_the_four_old_defects(self):
        rc = compose.materialize_rc()
        self.addCleanup(compose.remove, rc)
        done = subprocess.run(
            [sys.executable, str(QA_DIR / "baseline_probe.py")],
            cwd=str(rc), env={**_clean_env(),
                              "QA_BASELINE_ROOT": str(rc),
                              "PYTHONPATH": str(QA_DIR)},
            capture_output=True, text=True, timeout=180)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        result = json.loads(done.stdout)
        self.assertEqual(result["unknown_delivery"]["tick1_state"], "UNCONFIRMED")
        self.assertEqual(result["unknown_delivery"]["tick2_state"], "SETTLED")
        self.assertEqual(result["activation_blocked"]["state"], "BOUNDED_STOP")
        self.assertEqual(result["activation_blocked"]["exit"], 0)
        self.assertTrue(result["adapter_direct"].startswith("TypeError:"))
        self.assertEqual(result["ci_payload"]["delivered"], 0)
        self.assertIn("device_ref", result["ci_payload"]["why"])


class ReceiptTests(unittest.TestCase):
    def _core(self, scenario: Scenario, notifier):
        config = M["runtime"].load_config(json.loads(
            scenario.config_path.read_text(encoding="utf-8")))
        source = M["runtime"].build_source(
            config, offline_facts=scenario.facts_path)
        return M["core"](source=source, notifier=notifier,
                         state=M["state"](scenario.state_path),
                         watches=config["watches"], debounce_ticks=1)

    def test_unknown_delivery_stays_unconfirmed_then_reconciles_once(self):
        scenario = Scenario(self)
        scenario.write_facts(runs={RUN: verified_run()})
        scenario.write_config([run_watch()], views=["run"])

        for index in range(2):
            code, result, done = scenario.run_activation(
                extra_env={"QA_PROVIDER_MODE": "unknown"})
            self.assertEqual(code, 4, done.stderr)
            self.assertEqual(result["state"], "UNCONFIRMED",
                             f"tick {index + 1} settled without a receipt")
            self.assertEqual(result["health"]["unknown_receipts"], 1)

        rows = scenario.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "DELIVERY_UNKNOWN")
        state = scenario.state_document()
        cursor = next(iter(state["cursor"].values()))
        self.assertEqual(cursor["delivery"], "UNCONFIRMED")
        self.assertEqual(cursor["delivery_state"], "DELIVERY_UNKNOWN")

        code, result, done = scenario.run_monitor(
            extra_env={"QA_PROVIDER_MODE": "deliver"})
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(result["state"], "SETTLED")
        self.assertEqual(result["delivered"], 1)
        self.assertEqual(scenario.ledger_rows()[0]["state"], "DELIVERED")

        code, result, done = scenario.run_monitor(
            extra_env={"QA_PROVIDER_MODE": "deliver"})
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(result["duplicate"], 1)
        self.assertEqual(result["delivered"], 0)

        keys = {row["idempotency_key"] for row in scenario.journal()}
        self.assertEqual(len(keys), 1, f"distinct notification keys: {keys}")
        self.assertTrue(next(iter(keys)).startswith("ms1:"))
        self.assertEqual(len(next(iter(keys))), 50)

        before = len(scenario.journal())
        scenario.state_path.unlink()
        if scenario.lock_path.exists():
            scenario.lock_path.unlink()
        code, result, done = scenario.run_monitor(
            extra_env={"QA_PROVIDER_MODE": "deliver"})
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(result["duplicate"], 1)
        self.assertEqual(len(scenario.journal()), before,
                         "a lost cursor caused a second notification")

    def test_a_receipt_for_another_identity_does_not_settle(self):
        scenario = Scenario(self)
        scenario.write_facts(runs={RUN: verified_run()})
        scenario.write_config([run_watch()], views=["run"])

        class WrongIdentity:
            def observe_payload(self, source, payload):
                return {"state": "DELIVERED", "delivered": True,
                        "remote_id": "om_wrong",
                        "delivery_key":
                            "milestone:cloudcli:device:other:RUN_COMPLETED:"
                            "run-other:VERIFIED_GREEN"}

        tick = self._core(scenario, WrongIdentity()).run_once()
        self.assertEqual(tick["state"], "UNCONFIRMED")
        self.assertEqual(tick["delivered"], 0)
        self.assertEqual(tick["watches"][0]["receipt"], "IDENTITY_MISMATCH")
        self.assertNotEqual(tick["state"], "SETTLED")

    def test_a_receipt_without_a_delivery_key_does_not_settle(self):
        scenario = Scenario(self)
        scenario.write_facts(runs={RUN: verified_run()})
        scenario.write_config([run_watch()], views=["run"])

        class NoKey:
            def observe_payload(self, source, payload):
                return {"state": "DELIVERED", "delivered": True,
                        "remote_id": "om_no_key"}

        tick = self._core(scenario, NoKey()).run_once()
        self.assertEqual(tick["state"], "UNCONFIRMED")
        self.assertEqual(tick["delivered"], 0)
        self.assertEqual(tick["watches"][0]["receipt"], "DELIVERY_UNKNOWN")
        self.assertNotEqual(tick["state"], "SETTLED")


class CiSubjectTests(unittest.TestCase):
    def test_a_granted_project_is_delivered_as_device_ci_project(self):
        scenario = Scenario(self)
        scenario.write_facts(ci={SHA: green_ci()})
        scenario.write_config([ci_watch()], views=["ci"],
                              devices=[], sessions=[], projects=["serveros"])
        code, tick, done = scenario.run_monitor()
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(tick["delivered"], 1)
        journal = scenario.journal()
        self.assertEqual(len(journal), 1)
        self.assertIn("device:ci:serveros", journal[0]["text"])
        rows = scenario.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertIn("device:ci:serveros", rows[0]["delivery_key"])

    def test_an_ungranted_project_is_denied_before_any_read(self):
        scenario = Scenario(self)
        scenario.write_facts(ci={SHA: green_ci()})
        scenario.write_config([ci_watch(project="other")], views=["ci"],
                              devices=[], sessions=[], projects=["serveros"])
        code, tick, done = scenario.run_monitor()
        self.assertIn(code, (0, 4), done.stderr)
        self.assertEqual(tick["dispatched"], 0)
        self.assertEqual(tick["delivered"], 0)
        self.assertEqual(tick["reads"], 0)
        self.assertEqual(tick["watches"][0]["status"], "DENIED")
        self.assertEqual(tick["watches"][0]["reason"], "NOT_GRANTED")
        self.assertEqual(scenario.journal(), [])

    def test_an_untyped_declared_ci_subject_is_refused(self):
        scenario = Scenario(self)
        scenario.write_facts(ci={SHA: green_ci()})
        scenario.write_config([ci_watch(device_ref="victim")], views=["ci"],
                              devices=[], sessions=[], projects=["serveros"])
        code, tick, done = scenario.run_monitor()
        self.assertEqual(code, 3, done.stderr)
        self.assertEqual(tick["state"], "BLOCKED_INPUT")
        self.assertEqual(tick["dispatched"], 0)
        self.assertEqual(tick["watches"][0]["receipt"], "BLOCKED_CI_SUBJECT")
        self.assertEqual(scenario.journal(), [])

    def test_a_ci_watch_cannot_mint_another_devices_subject(self):
        """Characterisation of the surviving authorization elevation.

        A CI watch may declare ``device_ref: "device:victim"``. The source
        authorizes only the *project*; the notifier then uses the declared
        device without checking it against the grant. The observation is real
        but the subject is not authorized. The next test states the owed
        behaviour; the patch belongs to #497.
        """
        scenario = Scenario(self)
        scenario.write_facts(ci={SHA: green_ci()})
        scenario.write_config([ci_watch(device_ref=VICTIM)], views=["ci"],
                              devices=[DEV], sessions=[], projects=["serveros"])
        code, tick, done = scenario.run_monitor()
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(tick["delivered"], 1)
        journal = scenario.journal()
        self.assertEqual(len(journal), 1)
        self.assertIn(VICTIM, journal[0]["text"])
        rows = scenario.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertIn(VICTIM, rows[0]["delivery_key"])

    @unittest.expectedFailure
    def test_a_ci_watch_must_not_deliver_under_an_ungranted_device(self):
        scenario = Scenario(self)
        scenario.write_facts(ci={SHA: green_ci()})
        scenario.write_config([ci_watch(device_ref=VICTIM)], views=["ci"],
                              devices=[DEV], sessions=[], projects=["serveros"])
        _code, tick, _done = scenario.run_monitor()
        self.assertEqual(tick["dispatched"], 0)
        self.assertEqual(tick["watches"][0]["receipt"], "BLOCKED_CI_SUBJECT")
        self.assertEqual(scenario.journal(), [])


class NegativeControlTests(unittest.TestCase):
    def _single(self, *, runs=None, ci=None, watch=None, views=("run",),
                devices=None, sessions=None, projects=None):
        scenario = Scenario(self)
        scenario.write_facts(runs=runs, ci=ci)
        scenario.write_config([watch or run_watch()], views=list(views),
                              devices=devices, sessions=sessions,
                              projects=projects)
        code, tick, done = scenario.run_monitor()
        self.assertIn(code, (0, 3, 4), done.stderr)
        self.assertEqual(tick["dispatched"], 0)
        self.assertEqual(tick["delivered"], 0)
        self.assertEqual(scenario.journal(), [])
        self.assertEqual(scenario.ledger_rows(), [])
        return tick

    def test_another_boot_is_unknown_and_not_sent(self):
        tick = self._single(runs={RUN: verified_run(boot="boot-other")})
        self.assertEqual(tick["watches"][0]["status"], "UNKNOWN")
        self.assertEqual(tick["watches"][0]["reason"], "BOOT_MISMATCH")

    def test_another_device_is_unknown_and_not_sent(self):
        tick = self._single(runs={RUN: verified_run(device="device:other")})
        self.assertEqual(tick["watches"][0]["status"], "UNKNOWN")
        self.assertEqual(tick["watches"][0]["reason"], "IDENTITY_MISMATCH")

    def test_wrong_ci_sha_is_unknown_and_not_sent(self):
        tick = self._single(ci={SHA: green_ci(head_sha=OTHER_SHA)},
                            watch=ci_watch(), views=("ci",), devices=[],
                            sessions=[], projects=["serveros"])
        self.assertEqual(tick["watches"][0]["status"], "UNKNOWN")
        self.assertEqual(tick["watches"][0]["reason"], "SHA_MISMATCH")

    def test_running_ci_and_draft_ci_are_not_sent(self):
        for name, fact, reason in (
                ("running", green_ci(status="in_progress"), None),
                ("draft", green_ci(draft=True), "DRAFT_CI")):
            with self.subTest(name):
                tick = self._single(ci={SHA: fact}, watch=ci_watch(),
                                    views=("ci",), devices=[], sessions=[],
                                    projects=["serveros"])
                self.assertNotIn(tick["watches"][0]["status"],
                                 ("TERMINAL_VERIFIED", "FAILED"))
                if reason:
                    self.assertEqual(tick["watches"][0]["reason"], reason)

    def test_native_activity_and_the_note_veto(self):
        cases = (
            ("message", verified_run(), "TERMINAL_VERIFIED", None),
            ("no-message", verified_run(message=None), "UNKNOWN",
             "NO_NATIVE_ACTIVITY"),
            ("note-veto", verified_run(
                note="there was no readable assistant message"), "UNKNOWN",
             "NO_NATIVE_ACTIVITY"),
        )
        for name, fact, status, reason in cases:
            with self.subTest(name):
                scenario = Scenario(self)
                scenario.write_facts(runs={RUN: fact})
                scenario.write_config([run_watch()], views=["run"])
                code, tick, done = scenario.run_monitor()
                self.assertIn(code, (0, 4), done.stderr)
                self.assertEqual(tick["watches"][0]["status"], status)
                if reason:
                    self.assertEqual(tick["watches"][0]["reason"], reason)
                    self.assertEqual(tick["dispatched"], 0)
                    self.assertEqual(scenario.journal(), [])

    def test_a_delivery_id_only_watch_cannot_form_a_run_payload(self):
        scenario = Scenario(self)
        scenario.write_facts(runs={"delivery:" + DEL: verified_run()})
        scenario.write_config([run_watch(run_id=None, delivery_id=DEL)],
                              views=["run"])
        code, tick, done = scenario.run_monitor()
        self.assertEqual(code, 4, done.stderr)
        self.assertEqual(tick["watches"][0]["status"], "TERMINAL_VERIFIED")
        self.assertEqual(tick["watches"][0]["receipt"], "DELIVERY_UNKNOWN")
        self.assertIn("run.runId is required",
                      tick["watches"][0].get("receipt_why", ""))
        self.assertEqual(scenario.journal(), [])

    @unittest.expectedFailure
    def test_a_delivery_id_only_watch_must_be_deliverable(self):
        scenario = Scenario(self)
        scenario.write_facts(runs={"delivery:" + DEL: verified_run()})
        scenario.write_config([run_watch(run_id=None, delivery_id=DEL)],
                              views=["run"])
        _code, tick, _done = scenario.run_monitor()
        self.assertEqual(tick["delivered"], 1)


class ActivationTests(unittest.TestCase):
    def test_a_blocked_tick_is_not_reported_as_bounded_stop(self):
        scenario = Scenario(self)
        code, result, done = scenario.run_activation(configured=False)
        self.assertEqual(code, 3, done.stderr)
        self.assertEqual(result["state"], "BLOCKED_INPUT")
        self.assertNotEqual(result["state"], "BOUNDED_STOP")
        self.assertIsNone(result["health"]["stale"])
        self.assertIsNone(result["health"]["cursor_age"])
        self.assertEqual(result["ticks_report"][0]["state"], "BLOCKED_INPUT")
        self.assertEqual(result["ticks_report"][0]["why"], "BLOCKED_RUNTIME_ENTRY")

    def test_an_unconfirmed_tick_keeps_exit_4(self):
        scenario = Scenario(self)
        scenario.write_facts(runs={RUN: verified_run()})
        scenario.write_config([run_watch()], views=["run"])
        code, result, done = scenario.run_activation(
            extra_env={"QA_PROVIDER_MODE": "unknown"})
        self.assertEqual(code, 4, done.stderr)
        self.assertEqual(result["state"], "UNCONFIRMED")
        self.assertEqual(result["health"]["unknown_receipts"], 1)
        self.assertFalse(result["completed"])

    def test_a_measured_stale_cursor_outranks_a_success_word(self):
        """A wrapper-measured age is authoritative when the tick is silent.

        The real #493 tick always states its own ``stale``, and #498 gives the
        producer's own answer precedence over the CLI flag (a fresh tick has
        just measured itself). This negative control uses a silent success row
        so the wrapper's *own* measurement is the only one available — the
        case the ``--cursor-age-seconds`` flag exists for.
        """
        activation = M["activation"]
        scenario = Scenario(self)
        activation_value = activation.Activation(
            legacy_root=str(scenario.legacy), next_root=str(scenario.next),
            state=str(scenario.state_path), lock=str(scenario.lock_path),
            port=19301, legacy_port=19300,
            service_name="serveros-next-agent-monitor",
            legacy_service_name="serveros-legacy",
            plugin_id="serveros-next-monitor",
            legacy_plugin_id="serveros-legacy", ticks=1)
        result = activation.run_bounded(
            activation_value,
            monitor_entry=lambda: {"state": "SETTLED", "exit": 0},
            legacy_read=activation.probe_true,
            next_write=activation.probe_false,
            cursor_age_seconds=7200.0)
        self.assertEqual(result["state"], "UNCONFIRMED")
        self.assertEqual(result["exit"], 4)
        self.assertTrue(result["health"]["stale"])
        self.assertEqual(result["health"]["cursor_age"], 7200.0)
        self.assertIn("STALE_MONITOR",
                      {warning["code"] for warning in result["warnings"]})
        self.assertFalse(result["completed"])

    def test_a_real_tick_that_says_not_stale_is_not_overridden(self):
        """Characterisation of #498's precedence rule.

        The real tick has just measured its own cursor; it reports
        ``stale: false``. The wrapper keeps the producer's answer even when the
        operator passes ``--cursor-age-seconds``. This is not a default (the
        producer measured), but it is why the measured-age control above uses a
        silent row.
        """
        scenario = Scenario(self)
        scenario.write_facts(runs={RUN: verified_run()})
        scenario.write_config([run_watch()], views=["run"])
        code, result, done = scenario.run_activation(cursor_age_seconds=7200)
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(result["health"]["cursor_age"], 7200.0)
        self.assertIs(result["health"]["stale"], False)
        self.assertEqual(result["state"], "BOUNDED_STOP")

    def test_a_tick_that_raises_is_tick_failed_exit_2(self):
        scenario = Scenario(self)
        scenario.write_facts(runs={RUN: verified_run()})
        scenario.write_config([run_watch()], views=["run"])
        code, result, done = scenario.run_activation(
            monitor_entry="batch_adapter:tick")
        self.assertEqual(code, 2, done.stderr)
        self.assertEqual(result["state"], "TICK_FAILED")
        self.assertEqual(result["exit"], 2)

    def test_ticks_report_carries_each_tick_verdict(self):
        scenario = Scenario(self)
        scenario.write_facts(runs={RUN: verified_run()})
        scenario.write_config([run_watch()], views=["run"])
        code, result, done = scenario.run_activation(ticks=2)
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(len(result["ticks_report"]), 2)
        self.assertEqual({row["state"] for row in result["ticks_report"]},
                         {"SETTLED"})

    def test_unknown_failed_expired_tick_words_are_unrecognised_nonzero(self):
        activation = M["activation"]
        scenario = Scenario(self)
        act = activation.Activation(
            legacy_root=str(scenario.legacy), next_root=str(scenario.next),
            state=str(scenario.state_path), lock=str(scenario.lock_path),
            port=19301, legacy_port=19300,
            service_name="serveros-next-agent-monitor",
            legacy_service_name="serveros-legacy",
            plugin_id="serveros-next-monitor",
            legacy_plugin_id="serveros-legacy", ticks=1)
        for word in ("UNKNOWN", "FAILED", "EXPIRED"):
            with self.subTest(word):
                result = activation.run_bounded(
                    act,
                    monitor_entry=lambda w=word: {"state": w, "exit": 0},
                    legacy_read=activation.probe_true,
                    next_write=activation.probe_false)
                self.assertEqual(result["state"], activation.UNRECOGNISED)
                self.assertNotEqual(result["exit"], 0)

    def test_an_expired_watch_arrives_as_ticked_exit_0(self):
        """Characterisation of the remaining supervisor gap.

        The real #493 tick calls an expired/unknown watch ``TICKED`` with
        ``EXIT_SETTLED`` (backlog, not failure). #498 carries that word and
        number through. A supervisor that reads only the exit code sees 0 for
        a watch that has no terminal verdict. The next test states the owed
        behaviour; the patch is shared by #493's exit mapping and #498's
        backlog handling.
        """
        for name, fact in (
                ("expired", {"raise": "EXPIRED"}),
                ("unknown", verified_run(message=None))):
            with self.subTest(name):
                scenario = Scenario(self)
                scenario.write_facts(runs={RUN: fact})
                scenario.write_config([run_watch()], views=["run"])
                code, result, done = scenario.run_activation()
                self.assertEqual(code, 0, done.stderr)
                self.assertEqual(result["state"], "TICKED")
                self.assertEqual(result["health"]["backlog"], 1)
                self.assertFalse(result["completed"])

    @unittest.expectedFailure
    def test_an_unobserved_watch_must_not_exit_zero_at_the_supervisor(self):
        for name, fact in (
                ("expired", {"raise": "EXPIRED"}),
                ("unknown", verified_run(message=None))):
            with self.subTest(name):
                scenario = Scenario(self)
                scenario.write_facts(runs={RUN: fact})
                scenario.write_config([run_watch()], views=["run"])
                code, result, _done = scenario.run_activation()
                self.assertNotEqual(code, 0)
                self.assertNotEqual(result["state"], "TICKED")


class AdapterCompatTests(unittest.TestCase):
    def _adapter(self, document: dict, *, boot_id: str = BOOT,
                 grant_devices=(DEV,), views=("run", "ci")):
        grant = M["source_adapter"].CallerGrant.from_mapping({
            "principal": "qa", "devices": list(grant_devices),
            "projects": ["serveros"], "sessions": [SESS],
            "views": list(views)})
        client = M["source_adapter"].FixtureLegacyClient(
            document, fleet_instance_id=INSTANCE, route_id=ROUTE,
            boot_id=boot_id)
        return M["source_adapter"].LegacyReadOnlySourceAdapter(client, grant)

    def test_the_compat_source_drives_the_real_493_core(self):
        scenario = Scenario(self)
        adapter = self._adapter({"run": verified_run()})
        compat = M["compat"].CompatMonitorSource(adapter)
        notifier = M["notify"].MilestoneNotifier(
            M["qa_fakes"].JournalProvider(scenario.journal_path),
            scenario.ledger_path, conversation_ref="chat:qa")
        core = M["core"](source=compat, notifier=notifier,
                         state=M["state"](scenario.state_path),
                         watches=[run_watch()], debounce_ticks=1)
        tick = core.run_once()
        self.assertEqual(tick["state"], "SETTLED")
        self.assertEqual(tick["delivered"], 1)
        self.assertEqual(tick["reads"], 1)
        self.assertEqual(len(scenario.journal()), 1)

    def test_the_direct_adapter_is_callable_by_observe_watch(self):
        adapter = self._adapter({"run": verified_run()})
        view = M["observe_watch"](adapter, run_watch())
        self.assertEqual(view["status"], "TERMINAL_VERIFIED")
        self.assertTrue(hasattr(view, "get"),
                        "the Observation must be a mapping #493's core can read")

    def test_the_adapter_honours_the_watch_boot_and_last_message(self):
        adapter = self._adapter({"run": verified_run(boot="boot-other")})
        view = adapter.observe_run(device_ref=DEV, session_ref=SESS,
                                   run_id=RUN, expected_boot_id=BOOT)
        self.assertEqual((view.status, view.reason),
                         ("UNKNOWN", "BOOT_MISMATCH"))
        good = self._adapter({"run": verified_run()})
        self.assertEqual(
            good.observe_run(device_ref=DEV, session_ref=SESS, run_id=RUN,
                             expected_boot_id=BOOT).status,
            "TERMINAL_VERIFIED")

    def test_denial_native_raises_and_compat_returns_a_denied_observation(self):
        adapter = self._adapter({"run": verified_run()},
                                grant_devices=("device:other",))
        with self.assertRaises(M["source_adapter"].SourceRefused) as caught:
            adapter.observe_run(device_ref=DEV, session_ref=SESS, run_id=RUN)
        self.assertEqual(caught.exception.code, "DENIED")
        compat = M["compat"].CompatMonitorSource(adapter)
        view = compat.observe_run(device_ref=DEV, session_ref=SESS, run_id=RUN)
        self.assertEqual((view["status"], view["reason"]),
                         ("DENIED", "NOT_GRANTED"))
        self.assertEqual(view["readingsUsed"], 0)

    def test_wrong_sha_is_native_identity_and_compat_sha(self):
        adapter = self._adapter({"ci": ci_for_499(head_sha=OTHER_SHA)})
        native = adapter.observe_ci(source_sha=SHA, project="serveros")
        self.assertEqual(native.reason, "IDENTITY_MISMATCH")
        compat = M["compat"].CompatMonitorSource(adapter)
        view = compat.observe_ci(source_sha=SHA, project="serveros")
        self.assertEqual((view["status"], view["reason"]),
                         ("UNKNOWN", "SHA_MISMATCH"))
        self.assertNotIn(view["status"], ("TERMINAL_VERIFIED", "FAILED"))

    def test_no_output_and_unknown_ci_are_not_upgraded(self):
        scenario = Scenario(self)
        adapter = self._adapter({"run": verified_run(message=None)})
        compat = M["compat"].CompatMonitorSource(adapter)
        notifier = M["notify"].MilestoneNotifier(
            M["qa_fakes"].JournalProvider(scenario.journal_path),
            scenario.ledger_path, conversation_ref="chat:qa")
        tick = M["core"](source=compat, notifier=notifier,
                         state=M["state"](scenario.state_path),
                         watches=[run_watch()], debounce_ticks=1).run_once()
        self.assertEqual(tick["watches"][0]["status"], "UNKNOWN")
        self.assertEqual(tick["dispatched"], 0)
        self.assertEqual(scenario.journal(), [])


class IdentityAndBatchTests(unittest.TestCase):
    def test_two_runs_keep_their_identities_apart(self):
        scenario = Scenario(self)
        scenario.write_facts(runs={
            RUN: verified_run(),
            RUN2: verified_run(run_id=RUN2, device=DEV2, session=SESS2,
                               delivery=DEL2, boot=BOOT2),
        })
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
        self.assertEqual(len(scenario.ledger_rows()), 2)
        self.assertEqual(len({row["delivery_key"]
                              for row in scenario.ledger_rows()}), 2)

    def test_batch_and_monitor_share_one_ledger(self):
        scenario = Scenario(self)
        path, filed = self._file_batch(scenario)
        batch_env = scenario.env(extra={"QA_PROVIDER_MODE": "deliver"})
        batch = subprocess.run([
            sys.executable, str(TREE / compose.BATCH_DIR / "cli.py"),
            "--registration", str(path),
            "--ledger", str(scenario.ledger_path),
            "--state", str(scenario.dir / "batch-state.json"),
            "--lock", str(scenario.dir / "batch.lock"),
            "--provider-factory", "qa_fakes:batch_provider",
            "--source-factory", "qa_fakes:batch_source",
        ], cwd=str(scenario.dir), env=batch_env,
            capture_output=True, text=True, timeout=120)
        self.assertEqual(batch.returncode, 0, batch.stderr)
        document = json.loads(batch.stdout)
        self.assertEqual(document["state"], "SETTLED")
        self.assertEqual(document["summary"]["state"], "DELIVERED")
        self.assertEqual(len(scenario.journal()), 2)
        scenario.write_facts(runs={RUN: verified_run()})
        scenario.write_config([run_watch()], views=["run"])
        code, tick, done = scenario.run_monitor()
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(tick["duplicate"], 1)
        self.assertEqual(tick["delivered"], 0)
        self.assertEqual(len(scenario.journal()), 2)
        self.assertEqual(len(scenario.ledger_rows()), 2)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
