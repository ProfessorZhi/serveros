"""Wave 8: the monitor-only runtime accepted offline, deterministically.

What this file is
-----------------
An end-to-end acceptance for the L1 monitor profile that runs on a laptop with no
device, no service and no network: build the real package, "unpack" it into a
temporary revisions root, run the resident monitor for five ticks against a FAKE
service manager, read the status socket back, stop it, and roll back.

`integrations/fleet-v1-monitor-acceptance/acceptance.py` does the judging and
runs nothing; this file runs everything and feeds it the observations. That split
is what the actuator gate asks for, and it is also what makes the acceptance
testable: every judgement can be fed a doctored observation.

The negative controls are the point
-----------------------------------
An acceptance that only ever sees a compliant binary is indistinguishable from
one that checks nothing. So the suite also drives:
  * a STUB binary that issues a restart — and requires the acceptance to reject it;
  * a mutated observation with one verb appended;
  * two revisions that are byte-identical, which a rollback cannot distinguish.

Layers, stated
--------------
CODE and UNIT are what this file produces. HOSTED_CI is whatever the workflow
reports on the exact head. REAL_DEVICE and PRODUCTION are `NOT_RUN` — nothing
here installs, starts, stops or reloads anything on a machine, and a green run
says nothing about an installed device.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = ROOT / "integrations" / "fleet-v1-monitor-acceptance" / "acceptance.py"
MAKEFILE = ROOT / "serverosd" / "Makefile"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.dont_write_bytecode = True
acceptance = _load("fleet_v1_monitor_acceptance", ACCEPTANCE)

#: `__LOG__` rather than `%s`: this template is also a SHELL script containing
#: `printf '%s'`, and Python's %-formatting consumed that specifier the first
#: time this file was written — the fake then logged its own format string and
#: never recorded an argv, so the negative control below could not fail. That is
#: the control doing its job.
FAKE_SYSTEMCTL = """#!/bin/sh
# A service manager that records everything and answers one failed unit.
printf '%s\\n' "$*" >> "__LOG__"
for a in "$@"; do
  case "$a" in
    show) printf 'LoadState=loaded\\nActiveState=failed\\nSubState=failed\\n'; exit 0 ;;
    restart|start|stop) exit 0 ;;
  esac
done
exit 0
"""


def build_package(destination: Path) -> Path:
    """Build the real package. Returns the directory that is the payload."""
    env = {**os.environ}
    proc = subprocess.run(["make", "-f", str(MAKEFILE), f"OUT={destination}", "-j2", "dist"],
                          cwd=str(ROOT), capture_output=True, text=True, timeout=900, env=env)
    if proc.returncode != 0:
        raise AssertionError(f"the package did not build:\n{proc.stdout[-2000:]}\n"
                             f"{proc.stderr[-2000:]}")
    return destination / "dist"


def _free_port_path(directory: Path) -> Path:
    return directory / "s.sock"


class MonitorAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("g++") is None or shutil.which("make") is None:
            raise unittest.SkipTest("no C++ toolchain on this host; the acceptance needs a "
                                    "built package (CODE, not a device)")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.package = build_package(cls.root / "build")
        cls.manifest = json.loads((cls.package / "PACKAGE.json").read_text(encoding="utf-8"))
        cls.binary = cls.package / "bin" / "serveros-native-core"

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    # -- fixtures ---------------------------------------------------------
    def _revision(self, name: str, source: Path | None = None) -> Path:
        """'Unpack' a package into revisions/<name>, the way a release would."""
        target = self.root / "revisions" / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source or self.package, target)
        return target

    def _fake_systemctl(self, log: Path) -> Path:
        path = self.root / "systemctl"
        path.write_text(FAKE_SYSTEMCTL.replace("__LOG__", str(log)), encoding="utf-8")
        path.chmod(0o755)
        return path

    def _manifest_file(self) -> Path:
        path = self.root / "desired-state.json"
        path.write_text(json.dumps({
            "schema": "serveros/desired-state.v1", "node": "acceptance-node",
            "principal": "acceptance", "observe_only": False, "generation": 1,
            "services": [{"id": "toy", "owner": "acceptance", "unit": "toy.service",
                          "desired": "running",
                          "recovery": {"allowed_verbs": ["restart"],
                                       "offline_recoverable": True}}],
        }), encoding="utf-8")
        return path

    def _env(self) -> dict:
        runtime = self.root / "xdg"
        (runtime / "serveros").mkdir(parents=True, exist_ok=True)
        return {**os.environ, "XDG_RUNTIME_DIR": str(runtime)}

    def _argv(self, binary: Path, sock: Path, systemctl: Path, *extra: str) -> list[str]:
        return [str(binary), "--monitor-only", "--manifest", str(self._manifest_file()),
                "--allow-unit", "toy.service", "--interval-seconds", "1",
                "--status-socket", str(sock), "--systemctl", str(systemctl), *extra]

    def _read_status(self, sock: Path, deadline: float = 15.0) -> dict:
        end = time.time() + deadline
        while not sock.exists() and time.time() < end:
            time.sleep(0.05)
        self.assertTrue(sock.exists(), "no status socket appeared")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10)
            client.connect(str(sock))
            client.sendall(b"GET /status\n")
            data = b""
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                data += chunk
        return json.loads(data)

    # -- the acceptance ---------------------------------------------------
    def test_the_monitor_profile_passes_the_acceptance(self):
        log = self.root / "calls.log"
        log.unlink(missing_ok=True)
        systemctl = self._fake_systemctl(log)
        rev = self._revision("rev-current")
        sock = _free_port_path(self.root)

        proc = subprocess.Popen(self._argv(rev / "bin" / "serveros-native-core",
                                           sock, systemctl, "--max-ticks", "5"),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=self._env())
        status = self._read_status(sock)          # read back WHILE it is resident
        stdout, stderr = proc.communicate(timeout=60)

        reports = [
            acceptance.evaluate_package(rev, self.manifest),
            acceptance.evaluate_monitor_run(status, log.read_text(encoding="utf-8").splitlines(),
                                            exit_code=proc.returncode, stopped=True,
                                            ticks_expected=1),
        ]
        result = acceptance.receipt(reports, provenance={
            "source_commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                            capture_output=True, text=True).stdout.strip(),
            "package_sha256": acceptance.sha256(rev / "PACKAGE.json"),
            "binary_sha256": acceptance.sha256(rev / "bin" / "serveros-native-core"),
        })
        self.assertTrue(result["ok"], "\n".join(result["failures"]) + f"\n{stderr[-800:]}")
        self.assertEqual(result["layers"]["real_device"], "NOT_RUN")
        self.assertEqual(result["layers"]["production"], "NOT_RUN")
        # The costs are the sample the receipt carries, not a claim in prose.
        self.assertGreater(status["monitor"]["rss_kb"], 0)
        self.assertGreaterEqual(status["monitor"]["tick_ms"], 0)

    def test_it_stops_cleanly_when_asked(self):
        """`--max-ticks` is not a stop; SIGTERM is. The boundary says safe stop."""
        log = self.root / "calls-stop.log"
        log.unlink(missing_ok=True)
        systemctl = self._fake_systemctl(log)
        sock = _free_port_path(self.root)
        rev = self._revision("rev-stop")
        proc = subprocess.Popen(self._argv(rev / "bin" / "serveros-native-core", sock, systemctl),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=self._env())
        self._read_status(sock)                   # it is resident
        began = time.time()
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=30)
        elapsed = time.time() - began
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertLess(elapsed, 5.0, "the monitor did not honour SIGTERM promptly")
        self.assertIn("stopped_by_request=1", stdout + stderr)
        self.assertNotIn("restart", log.read_text(encoding="utf-8"))

    def test_every_action_switch_is_refused_by_name(self):
        log = self.root / "calls-refuse.log"
        systemctl = self._fake_systemctl(log)
        rev = self._revision("rev-refuse")
        binary = rev / "bin" / "serveros-native-core"
        # A VALID value, so the refusal that fires is the monitor's and not the
        # value parser's: `--link-state x` is rejected by the parser before the
        # profile is ever consulted, which would make this test prove nothing.
        takes_value = {"--lease": "x.lease", "--state": "x.json", "--facts": "x.json",
                       "--link-state": "up", "--agent-state": "idle",
                       "--service-runtime": "serving", "--receipt": "x.receipt",
                       "--owner-map": "x.json"}
        for flag in acceptance.ACTION_FLAGS:
            with self.subTest(flag=flag):
                argv = [str(binary), "--manifest", str(self._manifest_file()),
                        "--monitor-only", flag]
                if flag in takes_value:
                    argv.append(takes_value[flag])
                proc = subprocess.run(argv, capture_output=True, text=True, timeout=30,
                                      env=self._env())
                report = acceptance.evaluate_refusal(flag, proc.returncode, proc.stderr)
                self.assertTrue(report.ok, report.failures)

    # -- the negative controls -------------------------------------------
    def test_a_stub_that_restarts_is_rejected(self):
        """The acceptance must fail a binary that is not the monitor."""
        log = self.root / "calls-stub.log"
        log.unlink(missing_ok=True)
        systemctl = self._fake_systemctl(log)
        stub = self.root / "stub-monitor"
        stub.write_text(
            "#!/bin/sh\n"
            f'"{systemctl}" --user show toy.service >/dev/null\n'
            f'"{systemctl}" --user restart toy.service >/dev/null\n'
            "echo '{\"profile\": \"monitor-only\", \"mode\": \"observe-only\"}'\n"
            "exit 0\n", encoding="utf-8")
        stub.chmod(0o755)
        proc = subprocess.run([str(stub)], capture_output=True, text=True, timeout=30)
        status = {"profile": "monitor-only", "mode": "observe-only", "ticks": 5,
                  "monitor": {"rss_kb": 1000, "cpu_ms_total": 10, "tick_ms": 1},
                  "plan": {"actions": 1}, "services": [{"unit": "toy.service", "active": "failed"}]}
        report = acceptance.evaluate_monitor_run(
            status, log.read_text(encoding="utf-8").splitlines(),
            exit_code=proc.returncode, stopped=True, ticks_expected=1)
        self.assertFalse(report.ok, "the acceptance accepted a binary that restarts")
        self.assertTrue(any("no verb" in failure for failure in report.failures),
                        report.failures)

    def test_one_appended_verb_fails_a_good_run(self):
        """Mutation control: the judgement is not vacuous."""
        calls = ["--user --no-pager --quiet show --property=ActiveState -- toy.service"]
        status = {"profile": "monitor-only", "mode": "observe-only", "ticks": 3,
                  "monitor": {"rss_kb": 1000, "cpu_ms_total": 10, "tick_ms": 1},
                  "plan": {"actions": 1}, "services": [{"unit": "toy.service", "active": "failed"}]}
        self.assertTrue(acceptance.evaluate_monitor_run(
            status, calls, exit_code=0, stopped=True, ticks_expected=1).ok)
        doctored = calls + ["--user --no-pager --quiet restart -- toy.service"]
        self.assertFalse(acceptance.evaluate_monitor_run(
            status, doctored, exit_code=0, stopped=True, ticks_expected=1).ok)

    def test_an_acting_profile_is_not_a_monitor_run(self):
        status = {"profile": "serve", "mode": "act", "ticks": 3,
                  "monitor": {"rss_kb": 1000, "cpu_ms_total": 10, "tick_ms": 1},
                  "plan": {"actions": 1}, "services": []}
        report = acceptance.evaluate_monitor_run(
            status, ["--user --no-pager --quiet show -- toy.service"],
            exit_code=0, stopped=True, ticks_expected=1)
        self.assertFalse(report.ok)

    def test_identical_revisions_cannot_prove_a_rollback(self):
        """A rebuild of the same source is byte-identical, and says so."""
        left = self._revision("rev-a")
        right = self._revision("rev-b")
        left_manifest = json.loads((left / "PACKAGE.json").read_text(encoding="utf-8"))
        right_manifest = json.loads((right / "PACKAGE.json").read_text(encoding="utf-8"))
        report = acceptance.evaluate_rollback(left_manifest, right_manifest)
        self.assertFalse(report.ok, "a byte-identical pair was accepted as a rollback")
        self.assertTrue(any("different builds" in failure for failure in report.failures),
                        report.failures)

    def test_two_different_builds_make_a_rollback_observable(self):
        left = self._revision("rev-x")
        right = self._revision("rev-y")
        # A synthetic second build: same manifest shape, different actuator bytes.
        with (right / "bin" / "serveros-native-core").open("ab") as stream:
            stream.write(b"\0")            # the digest changes; nothing executes it
        manifest = json.loads((right / "PACKAGE.json").read_text(encoding="utf-8"))
        for member in manifest["members"]:
            if member["path"] == "bin/serveros-native-core":
                member["sha256"] = acceptance.sha256(right / "bin" / "serveros-native-core")
        report = acceptance.evaluate_rollback(
            json.loads((left / "PACKAGE.json").read_text(encoding="utf-8")), manifest)
        self.assertTrue(report.ok, report.failures)


if __name__ == "__main__":
    unittest.main()
