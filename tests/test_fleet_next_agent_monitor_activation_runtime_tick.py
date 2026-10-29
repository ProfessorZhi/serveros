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
* configured against offline facts, the monitor's own vitals must arrive in
  `health` — never replaced by the wrapper's defaults.

Two ABI facts that the first version of this file got wrong, and that the
composition caught (Wave 16, shard 3, three tests):

1. **The monitor's counts are top-level; its `health` block is not.** #493/#497
   answer with `backlog` / `unknown_receipts` / `unsettled` beside `state`, and
   `health` carries exactly `last_tick`, `last_ok`, `cursor_age`,
   `consecutive_failures`, `stale`. Asking `health.reported` for `backlog` is
   reading a field the producer never put there — the wrapper reads the count
   from where it is, and so must the test.
2. **Two processes do not agree on an instant, they agree on everything else.**
   `health.last_tick` (a real clock), `health.cursor_age` and the producer's
   timestamps differ *because the clocks ran*. Comparing whole documents byte for
   byte made those three tests fail on perfectly correct behaviour — which is
   itself the proof that the clock is live, so they now compare a projection
   with the time-bearing fields removed and check those fields against their own
   properties (a real epoch, a non-negative age, an ISO instant, and two
   readings that are not the same constant).

The `#493` package is a sibling PR, so this file *skips* — with the reason
printed — when it is not composed into the tree, and it skips when the host
cannot run it at all (`#493`'s state module is POSIX-only: `fcntl`, and an
`os.open` of a directory for the fsync). It never substitutes a stub for the
monitor: a fake would have made the P0 invisible again, and the exact document
shapes are pinned as literals in
`test_fleet_next_agent_monitor_activation_truth.py` instead, where they run on
every host.

To run it for real, compose #493 next to this branch (the RC #500, and the QA
#495/#501 compositions) on a POSIX host, or point
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


#: The readings that are an *instant*: two processes run the same monitor at
#: different moments, so these differ by design. Everything else must not.
TIME_BEARING = frozenset({"last_tick", "last_ok", "cursor_age"})

#: #497's `health` block, key for key. The counts (`backlog`,
#: `unknown_receipts`, `unsettled`) are *beside* it, at the tick's top level.
PRODUCER_HEALTH_KEYS = frozenset({"last_tick", "last_ok", "cursor_age",
                                  "consecutive_failures", "stale"})

ISO_INSTANT = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$"


def stable(value):
    """The document with every wall-clock reading removed, and nothing else.

    Removing them does not weaken the check below: each is then asserted on its
    own against a property a default cannot satisfy — a real epoch (`> 1e9`), a
    non-negative age, an ISO instant from the producer, and two processes whose
    timestamps are not the same constant. A wrapper that returned `0.0` or `None`
    still fails; it fails on the field instead of on a byte comparison with a
    sibling process's clock.
    """
    if isinstance(value, dict):
        return {key: stable(item) for key, item in value.items()
                if key not in TIME_BEARING}
    if isinstance(value, list):
        return [stable(item) for item in value]
    return value


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
            self.assertGreater(document["health"]["last_tick"], 1e9,
                               "the wrapper passed no clock, so `last_tick` was `0.0`")

        # Three processes run at three instants, so their `last_tick` values are
        # *supposed* to differ: compare everything that is not a clock.
        self.assertEqual(stable(results[0]), stable(results[1]),
                         "two processes disagreed about something other than the clock")
        self.assertEqual(stable(results[1]), stable(results[2]),
                         "two processes disagreed about something other than the clock")

        # ... and the clock is shown to be live, so the comparison above is not
        # passing merely because every process wrote the same placeholder.
        stamps = [document["health"]["last_tick"] for document in results]
        self.assertEqual(stamps, sorted(stamps), f"a wall clock went backwards: {stamps}")
        self.assertGreaterEqual(len(set(stamps)), 2,
                                "three processes reported one instant; that is a "
                                f"constant, not a clock: {stamps}")


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
        self.assertEqual([record["index"] for record in document["ticks_report"]], [0, 1])

        # The monitor observed one run that is still RUNNING: progress, not
        # completion. #493 says TICKED (exit 0); the wrapper must not translate
        # that into its own `BOUNDED_STOP`.
        self.assertEqual(document["state"], "TICKED", document)
        self.assertIs(document["completed"], False)
        self.assertEqual(code, 0, "TICKED is #493's exit 0, carried unchanged")

        health = document["health"]
        reported = health["reported"]

        # The producer's health block, key for key — these five and no others.
        # #497 states its counts *beside* `state`, at the tick's top level, so
        # asking `reported` for `backlog` reads a field the monitor never wrote.
        # That is the Wave 16 mistake this pins.
        self.assertEqual(set(reported), set(PRODUCER_HEALTH_KEYS),
                         "the wrapper rewrote the monitor's health block")
        self.assertNotIn("backlog", reported,
                         "#497 keeps `backlog` at the tick's top level, not in `health`")

        # ... and the counts really are carried, from where they live.
        self.assertEqual(health["backlog"], 1)
        self.assertEqual(health["unknown_receipts"], 0)
        self.assertEqual(document["ticks_report"][-1]["backlog"], 1,
                         "the per-tick record lost the count the tick reported")

        # A reading, not a default: the monitor timed its own cursor on this
        # tick, and one that could not would have written `null`.
        self.assertRegex(reported["last_tick"], ISO_INSTANT,
                         "the producer's own `last_tick` is not a UTC instant")
        self.assertRegex(reported["last_ok"], ISO_INSTANT,
                         "a successful tick records when it was last ok")
        self.assertIsInstance(reported["cursor_age"], (int, float))
        self.assertGreaterEqual(reported["cursor_age"], 0.0)
        self.assertEqual(reported["consecutive_failures"], 0)
        self.assertIs(reported["stale"], False,
                      "the producer measured this tick, so `False` is a reading")

        # The wrapper carried the producer's staleness and age instead of
        # deriving its own: #497 knows its configured bound, this wrapper does not.
        self.assertIs(health["stale"], False, "the producer's own staleness must win")
        self.assertEqual(health["cursor_age"], float(reported["cursor_age"]),
                         "the wrapper replaced the age it was given")
        self.assertGreater(health["last_tick"], 1e9,
                           "the wrapper passed no clock, so `last_tick` was `0.0`")

        # The monitor's warnings arrive as given, not summarised away.
        codes = [warning["code"] for warning in document["warnings"]]
        self.assertIn("BACKLOG", codes, "the monitor's own warning was dropped")
        for record in document["ticks_report"]:
            for warning in record["warnings"]:
                self.assertIn(warning, document["warnings"],
                              f"tick {record['index']}'s warning did not reach the "
                              "aggregate")

    def test_a_second_process_does_not_inherit_the_first_ones_verdict(self):
        first_code, first, err = self.run_wrapper(env=self.configured_env())
        self.assertEqual(err.strip(), "", err)
        second_code, second, err = self.run_wrapper(env=self.configured_env())
        self.assertEqual(err.strip(), "", err)

        self.assertEqual(first_code, second_code)
        self.assertEqual(first["state"], second["state"])
        self.assertEqual(len(first["ticks_report"]), len(second["ticks_report"]))

        # Neither process inherits the other's verdict, and neither disagrees
        # with the monitor it ran — about anything except *when* it ran.
        self.assertEqual(stable(first), stable(second),
                         "two interpreters disagreed about the same monitor")

        # The clocks are shown to be real, so the projection above is not
        # vacuous: each process measured its own age and stamped its own
        # instant, and the two instants are not the same constant.
        for document in (first, second):
            self.assertIsInstance(document["health"]["cursor_age"], (int, float))
            self.assertGreaterEqual(document["health"]["cursor_age"], 0.0)
            self.assertGreater(document["health"]["last_tick"], 1e9)
        self.assertNotEqual(first["health"]["last_tick"], second["health"]["last_tick"],
                            "both processes stamped one instant: a constant was read "
                            "instead of a clock")


if __name__ == "__main__":
    unittest.main()
