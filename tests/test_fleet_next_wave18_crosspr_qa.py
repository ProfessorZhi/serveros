#!/usr/bin/env python3
"""Wave 18 independent cross-PR QA: RC #500 + #503 + #504.

The code under test is the **real git merge** of:

    80220a4 (#500 Wave 16 RC: #497 + #498 + #499 on #494)
      + b7f1f61 (#503: the three runtime-tick tests' ABI)
      + 6286bfb (#504: CI double grant, undecided != exit 0, delivery-id-only)

The fix files are checked against their author-head blob ids. The #500 tree is
used only as the RED baseline and as the source for mutation controls.

Wave 16's three `expectedFailure` findings are now ordinary tests:
* an ungranted CI device with a granted project is 0 reads / 0 sends;
* expired/unknown is `UNCONFIRMED`, while RUNNING stays `TICKED` (not FAILED);
* a delivery-id-only watch either aligns with a native `runId` or gets the
  named `BLOCKED_RUN_IDENTITY` refusal.

No `expectedFailure` is carried over from Wave 16. New assertions are ordinary
PASS/FAIL; the baseline probe and mutation probes keep them non-vacuous.
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
QA_DIR = REPO / "integrations" / "fleet-next-wave18-crosspr-qa"
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
            f"from {sorted(missing)}; run baseline_probe.py for the #500 "
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
        self.dir = Path(tempfile.mkdtemp(prefix="wave18-scenario-"))
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
                 note: str | None = None, status: str = "completed") -> dict:
    fact = {"instanceId": INSTANCE, "deviceRef": device,
            "sessionRef": session, "runId": run_id,
            "deliveryId": delivery, "bootId": boot,
            "status": status}
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
    def test_the_fix_files_are_exactly_the_author_head_blobs(self):
        for (pr, relative), expected in compose.FIX_BLOBS.items():
            with self.subTest(pr=pr, path=relative):
                path = TREE / relative
                self.assertTrue(path.is_file(), path)
                self.assertEqual(compose.git_blob_sha(path.read_bytes()), expected)
        notifier = (TREE / compose.INTEGRATION_DIRS[423]
                    / "milestone_notify.py").read_bytes()
        self.assertIn(b"def send_text", notifier)

    def test_baseline_500_reproduces_the_four_old_defects(self):
        rc = compose.baseline_500()
        self.addCleanup(compose.remove, rc)
        done = subprocess.run(
            [sys.executable, str(QA_DIR / "baseline_probe.py")],
            cwd=str(rc), env={**_clean_env(),
                              "QA_BASELINE_ROOT": str(rc),
                              "PYTHONPATH": str(QA_DIR)},
            capture_output=True, text=True, timeout=240)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        result = json.loads(done.stdout)
        self.assertEqual(result["ci_forged"]["delivered"], 1)
        self.assertIn(VICTIM, json.dumps(result["ci_forged"]["journal"]))
        self.assertEqual(result["expired"]["state"], "TICKED")
        self.assertEqual(result["expired"]["exit"], 0)
        self.assertEqual(result["delivery_only_native"]["delivered"], 0)
        self.assertEqual(result["delivery_only_no_run"]["state"], "UNCONFIRMED")
        self.assertEqual(result["delivery_only_no_run"]["watch"]["receipt"],
                         "DELIVERY_UNKNOWN")

    def test_the_runtime_tick_three_tests_execute_and_pass(self):
        path = (TREE / "tests"
                / "test_fleet_next_agent_monitor_activation_runtime_tick.py")
        self.assertTrue(path.is_file())
        for attempt in (1, 2):
            done = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                 str(path)], cwd=str(TREE),
                env={**_clean_env(), "PYTHONPATH": _pythonpath(),
                     "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True, text=True, timeout=240)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn("3 passed", done.stdout,
                          f"attempt {attempt}: {done.stdout}")
            self.assertNotIn("skipped", done.stdout,
                             f"attempt {attempt} skipped a runtime-tick test")

    def test_mutation_controls_detect_the_removed_fixes(self):
        for mode, case in (("ci-device", "ci-forged"),
                           ("unobserved", "expired")):
            with self.subTest(mode):
                done = subprocess.run(
                    [sys.executable, str(QA_DIR / "mutation_probe.py")],
                    cwd=str(TREE),
                    env={**_clean_env(), "QA_MUTATION": mode,
                         "PYTHONPATH": str(QA_DIR),
                         "PYTHONDONTWRITEBYTECODE": "1"},
                    capture_output=True, text=True, timeout=240)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                result = json.loads(done.stdout)
                self.assertEqual(result["mode"], mode)
                observed = result["result"]
                if mode == "ci-device":
                    self.assertEqual(observed["case"], "ci-forged")
                    self.assertEqual(observed["delivered"], 1)
                    self.assertIn(VICTIM, json.dumps(observed["journal"]))
                else:
                    self.assertEqual(observed["case"], "expired")
                    self.assertEqual(observed["state"], "TICKED")
                    self.assertEqual(observed["exit"], 0)


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

    def test_an_ungranted_device_with_a_granted_project_is_zero_reads_zero_sends(self):
        """Wave16 P0-A, now an ordinary assertion.

        The project is granted; the declared device is not. Both dimensions
        must be authorized: the source refuses before the budget and the client,
        so there are zero reads and zero sends, and the refusal has a name.
        """
        scenario = Scenario(self)
        scenario.write_facts(ci={SHA: green_ci()})
        scenario.write_config([ci_watch(device_ref=VICTIM)], views=["ci"],
                              devices=[DEV], sessions=[], projects=["serveros"])
        code, tick, done = scenario.run_monitor()
        self.assertEqual(code, 3, done.stderr)
        self.assertEqual(tick["state"], "BLOCKED_INPUT")
        self.assertEqual(tick["dispatched"], 0)
        self.assertEqual(tick["delivered"], 0)
        self.assertEqual(tick["reads"], 0)
        self.assertEqual(tick["watches"][0]["status"], "DENIED")
        self.assertEqual(tick["watches"][0]["receipt"], "BLOCKED_CI_SUBJECT")
        self.assertEqual(scenario.journal(), [])
        self.assertEqual(scenario.ledger_rows(), [])

    def test_an_ungranted_project_with_a_granted_device_is_denied(self):
        scenario = Scenario(self)
        scenario.write_facts(ci={SHA: green_ci()})
        scenario.write_config([ci_watch(device_ref=VICTIM, project="other")],
                              views=["ci"], devices=[VICTIM], sessions=[],
                              projects=["serveros"])
        code, tick, done = scenario.run_monitor()
        self.assertEqual(code, 4, done.stderr)
        self.assertEqual(tick["state"], "UNCONFIRMED")
        self.assertEqual(tick["dispatched"], 0)
        self.assertEqual(tick["reads"], 0)
        self.assertEqual(tick["watches"][0]["status"], "DENIED")
        self.assertEqual(scenario.journal(), [])

    def test_an_authorized_device_and_project_delivers_under_the_declared_device(self):
        scenario = Scenario(self)
        scenario.write_facts(ci={SHA: green_ci()})
        scenario.write_config([ci_watch(device_ref=VICTIM)], views=["ci"],
                              devices=[VICTIM], sessions=[], projects=["serveros"])
        code, tick, done = scenario.run_monitor()
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(tick["delivered"], 1)
        journal = scenario.journal()
        self.assertEqual(len(journal), 1)
        self.assertIn(VICTIM, journal[0]["text"])
        rows = scenario.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertIn(VICTIM, rows[0]["delivery_key"])


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

    def test_only_native_activity_or_a_bound_verified_artifact_is_verified(self):
        cases = (
            ("bound-artifact", {"artifacts": [
                {"verified": True, "sourceSHA": SHA}]}, "TERMINAL_VERIFIED", 1),
            ("unverified-artifact", {"artifacts": [
                {"verified": False, "sourceSHA": SHA}]}, "UNKNOWN", 0),
            ("artifact-other-sha", {"artifacts": [
                {"verified": True, "sourceSHA": OTHER_SHA}]}, "UNKNOWN", 0),
            ("no-evidence", {}, "UNKNOWN", 0),
        )
        for name, extra, status, delivered in cases:
            with self.subTest(name):
                scenario = Scenario(self)
                fact = verified_run(message=None)
                fact.update(extra)
                scenario.write_facts(runs={RUN: fact})
                scenario.write_config([run_watch(expected_source_sha=SHA)],
                                      views=["run"])
                code, tick, done = scenario.run_monitor()
                self.assertIn(code, (0, 4), done.stderr)
                self.assertEqual(tick["watches"][0]["status"], status)
                self.assertEqual(tick["delivered"], delivered)
                if delivered == 0:
                    self.assertEqual(scenario.journal(), [])

    def test_a_transcript_is_not_representable_by_the_493_offline_client(self):
        """Characterisation of a remaining protocol gap, not a Wave16 xfail.

        #499's adapter treats a transcript as native activity. #493's own
        offline facts client has a closed key set without `transcript`, so the
        same fact is refused before classification (`REMOTE_ValueError`). The
        compat view is tested separately and does accept it. Patch owner:
        #493/#499 to agree on one native-evidence shape.
        """
        scenario = Scenario(self)
        fact = verified_run(message=None)
        fact["transcript"] = [{"role": "assistant", "content": "native"}]
        scenario.write_facts(runs={RUN: fact})
        scenario.write_config([run_watch()], views=["run"])
        code, tick, done = scenario.run_monitor()
        self.assertIn(code, (0, 4), done.stderr)
        self.assertEqual(tick["watches"][0]["status"], "UNKNOWN")
        self.assertEqual(tick["watches"][0]["reason"], "REMOTE_ValueError")
        self.assertEqual(tick["dispatched"], 0)
        self.assertEqual(scenario.journal(), [])

    def test_a_delivery_id_only_watch_aligns_with_a_native_run_id(self):
        """Wave16 P1, fixed: the answer's own runId is adopted, not invented."""
        scenario = Scenario(self)
        scenario.write_facts(runs={"delivery:" + DEL: verified_run()})
        scenario.write_config([run_watch(run_id=None, delivery_id=DEL)],
                              views=["run"])
        code, tick, done = scenario.run_monitor()
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(tick["state"], "SETTLED")
        self.assertEqual(tick["delivered"], 1)
        journal = scenario.journal()
        self.assertEqual(len(journal), 1)
        self.assertIn(f"run:{RUN}", journal[0]["text"])
        rows = scenario.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertIn(RUN, rows[0]["delivery_key"])

    def test_a_delivery_id_only_watch_without_a_run_id_is_named_refused(self):
        """Wave16 P1, the other half: a fact that can never be delivered says
        so by name before any claim is written — not `run.runId is required`
        from #423 after the attempt."""
        scenario = Scenario(self)
        fact = verified_run()
        fact.pop("runId")
        scenario.write_facts(runs={"delivery:" + DEL: fact})
        scenario.write_config([run_watch(run_id=None, delivery_id=DEL)],
                              views=["run"])
        code, tick, done = scenario.run_monitor()
        self.assertEqual(code, 3, done.stderr)
        self.assertEqual(tick["state"], "BLOCKED_INPUT")
        self.assertEqual(tick["dispatched"], 0)
        self.assertEqual(tick["delivered"], 0)
        self.assertEqual(tick["watches"][0]["receipt"], "BLOCKED_RUN_IDENTITY")
        self.assertIn("no `runId`", tick["watches"][0].get("receipt_why", ""))
        self.assertEqual(scenario.journal(), [])
        self.assertEqual(scenario.ledger_rows(), [])


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

    def test_expired_unknown_and_never_issued_are_unconfirmed_nonzero(self):
        """Wave16 P0-B, now an ordinary assertion: blind is not zero.

        EXPIRED / no-native-activity / NEVER_ISSUED are not work in flight, so
        #504's `UNOBSERVED` path must keep them out of TICKED and out of exit 0.
        """
        for name, fact in (
                ("expired", {"raise": "EXPIRED"}),
                ("unknown", verified_run(message=None)),
                ("never-issued", None)):
            with self.subTest(name):
                scenario = Scenario(self)
                scenario.write_facts(runs={} if fact is None else {RUN: fact})
                scenario.write_config([run_watch()], views=["run"])
                code, result, done = scenario.run_activation()
                self.assertEqual(code, 4, done.stderr)
                self.assertEqual(result["state"], "UNCONFIRMED")
                self.assertNotEqual(result["state"], "TICKED")
                self.assertFalse(result["completed"])
                self.assertEqual(result["health"]["backlog"], 1)
                self.assertIn("UNOBSERVED",
                              {w["code"] for w in result["warnings"]})

    def test_a_running_watch_is_ticked_not_failed(self):
        """Work in flight is not a failure, and not an UNCONFIRMED either."""
        scenario = Scenario(self)
        scenario.write_facts(runs={RUN: verified_run(status="running")})
        scenario.write_config([run_watch()], views=["run"])
        code, result, done = scenario.run_activation()
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(result["state"], "TICKED")
        report = result["ticks_report"][0]
        self.assertEqual(report["state"], "TICKED")
        self.assertEqual(report["backlog"], 1)
        self.assertNotIn("UNOBSERVED",
                         {w["code"] for w in result["warnings"]})


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

    def test_the_compat_view_accepts_a_transcript_the_offline_client_cannot(self):
        """The #499 side of the protocol gap: a transcript is native activity
        there, and the compat view delivers it through the real core."""
        fact = verified_run(message=None)
        fact["transcript"] = [{"role": "assistant", "content": "native"}]
        adapter = self._adapter({"run": fact})
        compat = M["compat"].CompatMonitorSource(adapter)
        scenario = Scenario(self)
        notifier = M["notify"].MilestoneNotifier(
            M["qa_fakes"].JournalProvider(scenario.journal_path),
            scenario.ledger_path, conversation_ref="chat:qa")
        tick = M["core"](source=compat, notifier=notifier,
                         state=M["state"](scenario.state_path),
                         watches=[run_watch()], debounce_ticks=1).run_once()
        self.assertEqual(tick["state"], "SETTLED")
        self.assertEqual(tick["delivered"], 1)
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

    def test_shadow_create_and_send_are_zero(self):
        """The monitor path performs reads only; a fake client that would record
        a create/send is never asked for one."""
        from fleet_next_agent_monitor.sources import Grant, LegacyFactsSource

        class WriteTrap:
            fleet_instance_id = INSTANCE
            route_id = ROUTE
            boot_id = BOOT
            provider = "fake"

            def __init__(self):
                self.reads = 0
                self.writes = 0

            def read_run(self, **kwargs):
                self.reads += 1
                return verified_run()

            def read_ci(self, **kwargs):
                self.reads += 1
                return green_ci()

            def create_session(self, **kwargs):
                self.writes += 1
                raise AssertionError("shadow create_session was called")

            def send(self, **kwargs):
                self.writes += 1
                raise AssertionError("shadow send was called")

        scenario = Scenario(self)
        trap = WriteTrap()
        grant = Grant.from_mapping({
            "principal": "qa", "devices": [DEV], "projects": [],
            "sessions": [SESS], "views": ["run"]})
        notifier = M["notify"].MilestoneNotifier(
            M["qa_fakes"].JournalProvider(scenario.journal_path),
            scenario.ledger_path, conversation_ref="chat:qa")
        core = M["core"](source=LegacyFactsSource(trap, grant),
                         notifier=notifier,
                         state=M["state"](scenario.state_path),
                         watches=[run_watch()], debounce_ticks=1)
        tick = core.run_once()
        self.assertEqual(tick["delivered"], 1)
        self.assertEqual(trap.reads, 1)
        self.assertEqual(trap.writes, 0)
        self.assertEqual(len(scenario.journal()), 1)

    def test_an_instance_mismatch_is_unknown_and_not_sent(self):
        scenario = Scenario(self)
        fact = verified_run()
        fact["instanceId"] = "inst-other"
        scenario.write_facts(runs={RUN: fact})
        scenario.write_config([run_watch()], views=["run"])
        code, tick, done = scenario.run_monitor()
        self.assertIn(code, (0, 4), done.stderr)
        self.assertEqual(tick["watches"][0]["status"], "UNKNOWN")
        self.assertEqual(tick["watches"][0]["reason"], "INSTANCE_MISMATCH")
        self.assertEqual(tick["dispatched"], 0)
        self.assertEqual(scenario.journal(), [])

    def test_two_ci_shas_keep_their_source_sha_apart(self):
        scenario = Scenario(self)
        scenario.write_facts(ci={SHA: green_ci(),
                                 OTHER_SHA: green_ci(head_sha=OTHER_SHA)})
        scenario.write_config([
            ci_watch(),
            ci_watch(expected_source_sha=OTHER_SHA),
        ], views=["ci"], devices=[], sessions=[], projects=["serveros"])
        code, tick, done = scenario.run_monitor()
        self.assertEqual(code, 0, done.stderr)
        self.assertEqual(tick["delivered"], 2)
        journal = scenario.journal()
        self.assertEqual(len(journal), 2)
        first = next(row for row in journal if SHA in row["text"])
        second = next(row for row in journal if OTHER_SHA in row["text"])
        self.assertNotIn(OTHER_SHA, first["text"])
        self.assertNotIn(SHA, second["text"])
        state = scenario.state_document()
        self.assertEqual(len(state["cursor"]), 2)
        by_sha = {}
        for key, row in state["cursor"].items():
            by_sha[key.rsplit("|", 1)[-1]] = row["fingerprint"]
        self.assertIn(SHA, by_sha[SHA])
        self.assertNotIn(OTHER_SHA, by_sha[SHA])
        self.assertIn(OTHER_SHA, by_sha[OTHER_SHA])
        self.assertNotIn(SHA, by_sha[OTHER_SHA])

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
