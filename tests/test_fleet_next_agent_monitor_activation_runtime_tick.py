#!/usr/bin/env python3
"""The activation wrapper against #493's *real* `runtime:tick`, process by process.

WHY THIS FILE EXISTS
--------------------
Every other activation test injects a Python callable, which is the wrapper's
declared seam but not the thing a systemd user unit runs. #495 measured the P0 in
the composed shape: three separate interpreters, `--monitor-entry
fleet_next_agent_monitor.runtime:tick`, and a wrapper that dropped the tick's
verdict and answered `BOUNDED_STOP` / exit 0 no matter what the monitor said.

So these tests run the real thing in separate processes and assert on what a
supervisor would actually receive:

* unconfigured (`FLEET_NEXT_AGENT_MONITOR_CONFIG`/`_STATE` absent) the monitor
  answers `BLOCKED_INPUT` exit 3 — the wrapper must forward that word and that
  code, in every process, and must not claim `health.stale: false` for a fact
  nothing measured;
* configured against offline facts, the monitor's own vitals (`backlog`,
  `cursor_age`, `last_tick`, `stale`) must arrive in `health` — verbatim under
  `health.reported`, and never replaced by the wrapper's defaults.

The `#493` package is a sibling PR, so this file *skips* — with the reason
printed — when it is not composed into the tree, and it skips when the host
cannot run it at all (`#493`'s state module is POSIX-only: `fcntl`, and an
`os.open` of a directory for the fsync). It never substitutes a stub for the
monitor: a fake would have made the P0 invisible again, and the exact document
shapes are pinned as literals in
`test_fleet_next_agent_monitor_activation_truth.py` instead, where they run on
every host.

To run it for real, compose #493 next to this branch (the RC #494 and the QA
#495 compositions already do) on a POSIX host, or point
`SERVEROS_FLEET_NEXT_MONITOR_ROOT` at a checkout of
`integrations/fleet-next-agent-monitor`.

`#495`'s own `CrossProcessTests`/`ActivationReportingTests` call the same seam
(`activation.main(argv)` from `integrations/fleet-next-monitor-crosspr-qa`), so
its cases remain reusable here unchanged.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ACTIVATION_DIR = REPO / "integrations" / "fleet-next-agent-monitor-activation"
CORE_DIR = REPO / "integrations" / "fleet-next-agent-monitor"

CONFIG_SCHEMA = "serveros/fleet-next-agent-monitor-config.v1"

RUNNER = '''\
import json, os, sys
for root in json.loads(os.environ["ACTIVATION_ROOTS"]):
    if root not in sys.path:
        sys.path.insert(0, root)
import activation
raise SystemExit(activation.main(sys.argv[1:]))
'''


def core_root() -> Path:
    """#493's package root, or a skip naming what is missing."""
    override = os.environ.get("SERVEROS_FLEET_NEXT_MONITOR_ROOT")
    if override:
        return Path(override)
    return CORE_DIR


class Base(unittest.TestCase):
    def setUp(self):
        root = core_root()
        package = root / "fleet_next_agent_monitor" / "runtime.py"
        if not package.is_file():
            self.skipTest(
                f"#493's monitor package is not composed here ({package}); this "
                "regression needs the real runtime:tick, not a stub")
        self.root = root
        self.tmp = Path(tempfile.mkdtemp(prefix="next-monitor-runtime-tick-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runner = self.tmp / "run_activation.py"
        self.runner.write_text(RUNNER, encoding="utf-8")
        self.legacy = self.tmp / "legacy"
        self.next_root = self.tmp / "next"
        self.state = self.next_root / "monitor-state.json"
        self.lock = self.next_root / "monitor.lock"
        for path in (self.legacy, self.next_root):
            path.mkdir(parents=True, exist_ok=True)
        self.require_importable()

    def require_importable(self) -> None:
        """Skip — naming the module — when this host cannot import #493 at all."""
        probe = subprocess.run(
            [sys.executable, "-c", "import fleet_next_agent_monitor.runtime"],
            env=self.env(), capture_output=True, text=True, timeout=120)
        if probe.returncode == 0:
            return
        lines = [line for line in probe.stderr.strip().splitlines() if line.strip()]
        self.skipTest("#493's monitor cannot run on this host: "
                      + (lines[-1] if lines else "import failed without a message"))

    def env(self, **extra) -> dict:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("FLEET_NEXT_AGENT_MONITOR_")}
        env["ACTIVATION_ROOTS"] = json.dumps([str(ACTIVATION_DIR)])
        env["PYTHONPATH"] = os.pathsep.join(
            [str(self.root), str(ACTIVATION_DIR)]
            + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else []))
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("PYTHONSTARTUP", None)
        env.update({key: str(value) for key, value in extra.items()})
        return env

    def argv(self, ticks: int = 1) -> list[str]:
        return [
            sys.executable, str(self.runner),
            "--legacy-root", str(self.legacy),
            "--next-root", str(self.next_root),
            "--state", str(self.state),
            "--lock", str(self.lock),
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

    def run_wrapper(self, *, ticks: int = 1,
                    env: dict | None = None) -> tuple[int, dict, str]:
        done = subprocess.run(self.argv(ticks), cwd=str(self.tmp),
                              env=env if env is not None else self.env(),
                              capture_output=True, text=True, timeout=180)
        document = json.loads(done.stdout) if done.stdout.strip() else {}
        return done.returncode, document, done.stderr


class TheRealRefusalReachesTheSupervisor(Base):
    def test_three_processes_report_the_blocked_tick_and_none_of_them_flattens_it(self):
        """No config in the environment: the monitor has nothing to read.

        That is #493's `BLOCKED_INPUT` exit 3, and #495 measured the wrapper
        answering `BOUNDED_STOP` exit 0 to it.
        """
        results = []
        for _ in range(3):
            code, document, err = self.run_wrapper()
            self.assertEqual(err.strip(), "", f"a clean refusal, not a crash: {err}")
            self.assertEqual(code, 3, f"the wrapper's exit must be the monitor's: {document}")
            results.append(document)

        for document in results:
            self.assertEqual(document["state"], "BLOCKED_INPUT")
            self.assertEqual(document["exit"], 3)
            self.assertIs(document["completed"], False)
            self.assertIn("BLOCKED_RUNTIME_ENTRY", document["why"])
            self.assertIs(document["health"]["stale"], None,
                          "nothing measured staleness, so nothing may say it is fine")
            self.assertGreater(document["health"]["last_tick"], 1e9)

        self.assertEqual(results[0], results[1], "two processes disagreed")
        self.assertEqual(results[1], results[2], "two processes disagreed")


class TheRealVitalsSurviveTheWrapper(Base):
    def setUp(self):
        super().setUp()
        self.config = self.tmp / "monitor-config.json"
        self.config.write_text(json.dumps({
            "schema": CONFIG_SCHEMA,
            "grant": {"principal": "qa", "devices": ["dev-1"],
                      "projects": ["serveros"], "sessions": ["sess-1"],
                      "views": ["run"]},
            "watches": [{"kind": "run", "device_ref": "dev-1",
                         "session_ref": "sess-1", "run_id": "run-1"}],
        }), encoding="utf-8")
        self.facts = self.tmp / "offline-facts.json"
        self.facts.write_text(json.dumps({
            "fleet_instance_id": "inst-1", "route_id": "route-1",
            "boot_id": "boot-1",
            "runs": {"run-1": {"instanceId": "inst-1", "deviceRef": "dev-1",
                               "sessionRef": "sess-1", "runId": "run-1",
                               "deliveryId": "del-1", "bootId": "boot-1",
                               "status": "RUNNING",
                               "lastAssistantMessage": None}},
        }), encoding="utf-8")

    def configured_env(self) -> dict:
        return self.env(
            FLEET_NEXT_AGENT_MONITOR_CONFIG=self.config,
            FLEET_NEXT_AGENT_MONITOR_STATE=self.state,
            FLEET_NEXT_AGENT_MONITOR_OFFLINE_FACTS=self.facts)

    def test_a_configured_tick_carries_its_vitals_through_the_wrapper(self):
        code, document, err = self.run_wrapper(ticks=2, env=self.configured_env())
        self.assertEqual(err.strip(), "", err)

        self.assertEqual(document["ticks"], 2)
        self.assertEqual(len(document["ticks_report"]), 2)

        # The monitor observed one run that is still RUNNING: progress, not
        # completion. #493 says TICKED (exit 0); the wrapper must not translate
        # that into its own `BOUNDED_STOP`.
        self.assertEqual(document["state"], "TICKED", document)
        self.assertIs(document["completed"], False)
        self.assertEqual(code, 0, "TICKED is #493's exit 0, carried unchanged")

        self.assertEqual(document["health"]["reported"]["backlog"], 1)
        self.assertEqual(document["health"]["backlog"], 1)
        self.assertIsNotNone(document["health"]["reported"]["last_tick"])
        self.assertIsNotNone(document["health"]["reported"]["last_ok"])
        self.assertIsInstance(document["health"]["reported"]["cursor_age"], (int, float))
        self.assertGreater(document["health"]["last_tick"], 1e9)

    def test_a_second_process_does_not_inherit_the_first_ones_verdict(self):
        first_code, first, err = self.run_wrapper(env=self.configured_env())
        self.assertEqual(err.strip(), "", err)
        second_code, second, err = self.run_wrapper(env=self.configured_env())
        self.assertEqual(err.strip(), "", err)
        self.assertEqual(first_code, second_code)
        self.assertEqual(first["state"], second["state"])
        self.assertEqual(first["ticks_report"], second["ticks_report"],
                         "two interpreters disagreed about the same monitor")


if __name__ == "__main__":
    unittest.main()
