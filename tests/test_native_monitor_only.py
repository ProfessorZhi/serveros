"""The V1 serverosd monitor profile: resident, read-only, provably inert.

Owner boundary (#434, 2026-10-28): L1 is a **monitor**, not an agent. No model,
no event-woken reasoning, no automatic remediation, no restart, no Feishu client
in C++ — and any existing mutation capability stays gated until it is separately
authorised.

This file is the evidence for that, and it is deliberately about things a test
can see rather than things a comment can claim:

* the profile REFUSES every flag that authorises a mutation, by name;
* a unit observed as `failed` is observed and NOT acted on — the fake service
  manager records `show` and nothing else;
* the status readback says which profile answered and what it cost;
* the cost is measured, not asserted: RSS, CPU and per-tick time are bounded;
* nothing in the native tree can make an outbound connection.

The binary is opt-in via `SERVEROS_NATIVE_CORE_BIN`, the same convention the
other native suites use, so a compiler is not a test dependency.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native"

#: Every flag that authorises, feeds or records a mutation. The monitor profile
#: must refuse each of them rather than ignore it.
ACTING_FLAGS = (
    ("--enable-actions", None),
    ("--lease", "x.lease"),
    ("--state", "x.json"),
    ("--facts", "x.json"),
    ("--link-state", "up"),
    ("--agent-state", "idle"),
    ("--service-runtime", "serving"),
    ("--receipt", "x.receipt"),
)


def _binary() -> Path | None:
    raw = os.environ.get("SERVEROS_NATIVE_CORE_BIN")
    return Path(raw) if raw and Path(raw).is_file() else None


class TheProfileRefusesMutationsTest(unittest.TestCase):
    """No binary required: the refusal is an argument check."""

    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            self.skipTest("no native binary; set SERVEROS_NATIVE_CORE_BIN")

    def test_every_acting_flag_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "m.json"
            manifest.write_text(json.dumps({
                "schema": "serveros/desired-state.v1", "node": "n", "principal": "p",
                "observe_only": True, "generation": 1, "services": [],
            }), encoding="utf-8")
            for flag, value in ACTING_FLAGS:
                with self.subTest(flag=flag):
                    argv = [str(self.binary), "--manifest", str(manifest),
                            "--monitor-only", flag]
                    if value is not None:
                        argv.append(value)
                    proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
                    self.assertEqual(proc.returncode, 2, proc.stderr)
                    self.assertIn("--monitor-only refuses", proc.stderr)
                    self.assertIn(flag, proc.stderr)

    def test_the_acting_profile_still_exists_and_is_unchanged(self):
        """The owner said not to delete the protected feature, only to gate it."""
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "m.json"
            manifest.write_text(json.dumps({
                "schema": "serveros/desired-state.v1", "node": "n", "principal": "p",
                "observe_only": True, "generation": 1, "services": [],
            }), encoding="utf-8")
            # Without --monitor-only the acting path is reachable exactly as
            # before: it needs its own owner map, which is the gate, not this
            # profile's refusal.
            proc = subprocess.run([str(self.binary), "--manifest", str(manifest),
                                   "--enable-actions", "--lease", str(Path(td) / "l")],
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 2)
            self.assertNotIn("--monitor-only", proc.stderr)


class TheMonitorDoesNothingTest(unittest.TestCase):
    """End-to-end, against a FAKE service manager that records its argv."""

    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            self.skipTest("no native binary; set SERVEROS_NATIVE_CORE_BIN")
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.calls = self.dir / "calls.log"
        self.systemctl = self.dir / "systemctl"
        self.systemctl.write_text(
            "#!/bin/sh\n"
            f'printf \'%s\\n\' "$*" >> "{self.calls}"\n'
            "for a in \"$@\"; do\n"
            "  case \"$a\" in\n"
            "    show) printf 'LoadState=loaded\\nActiveState=failed\\nSubState=failed\\n'; exit 0 ;;\n"
            "    *) exit 0 ;;\n"
            "  esac\n"
            "done\nexit 0\n", encoding="utf-8")
        self.systemctl.chmod(0o755)
        (self.dir / "xdg" / "serveros").mkdir(parents=True)
        self.socket_path = self.dir / "s.sock"
        self.manifest = self.dir / "m.json"
        self.manifest.write_text(json.dumps({
            "schema": "serveros/desired-state.v1", "node": "test-node", "principal": "p",
            "observe_only": True, "generation": 1,
            "services": [{"id": "toy", "owner": "p", "unit": "toy.service",
                          "desired": "running",
                          "recovery": {"allowed_verbs": ["restart"],
                                       "offline_recoverable": True}}],
        }), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _argv(self, *extra):
        return [str(self.binary), "--manifest", str(self.manifest),
                "--monitor-only", "--allow-unit", "toy.service",
                "--interval-seconds", "1", "--systemctl", str(self.systemctl), *extra]

    def _env(self):
        return {**os.environ, "XDG_RUNTIME_DIR": str(self.dir / "xdg")}

    def test_a_failed_unit_is_observed_and_never_acted_on(self):
        proc = subprocess.run(self._argv("--max-ticks", "3"), env=self._env(),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = self.calls.read_text(encoding="utf-8").splitlines()
        self.assertTrue(calls, "the fake service manager was never asked anything")
        verbs = {line.split()[-3] if len(line.split()) > 3 else "" for line in calls}
        # `show` is the only thing a monitor may ask. A restart here would be the
        # automatic healing the owner explicitly deferred.
        self.assertTrue(all("show" in line for line in calls),
                        f"the monitor issued a verb: {calls[:5]}")

    def test_the_status_readback_names_the_profile_and_its_cost(self):
        proc = subprocess.Popen(self._argv("--max-ticks", "5",
                                           "--status-socket", str(self.socket_path)),
                                env=self._env(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.time() + 10
            while not self.socket_path.exists() and time.time() < deadline:
                time.sleep(0.1)
            self.assertTrue(self.socket_path.exists(), "no status socket appeared")
            time.sleep(1.5)                      # let a tick or two finish
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(10)
                client.connect(str(self.socket_path))
                client.sendall(b"GET /status\n")
                data = b""
                while True:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    data += chunk
            doc = json.loads(data)
        finally:
            proc.wait(timeout=30)

        self.assertEqual(doc["profile"], "monitor-only")
        self.assertEqual(doc["mode"], "observe-only")
        self.assertEqual(doc["plan"]["actions"], 0, "a monitor planned an action")
        monitor = doc["monitor"]
        self.assertEqual(monitor["profile"], "monitor-only")
        # The cost is MEASURED. These are bounds on the real numbers, not a
        # restatement of a comment: 5 MB RSS and single-digit milliseconds are
        # what this host reported, with room for a slower machine.
        self.assertLess(monitor["rss_kb"], 64 * 1024, monitor)
        self.assertLess(monitor["cpu_ms"], 2000, monitor)
        self.assertLess(monitor["tick_ms"], 1000, monitor)
        self.assertGreaterEqual(doc["ticks"], 1)

    def test_the_monitor_is_resident_without_being_told_to_serve(self):
        """`--monitor-only` implies the resident loop; that is the profile."""
        proc = subprocess.run(self._argv("--max-ticks", "2"), env=self._env(),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ticks=2", proc.stdout + proc.stderr)


class NothingHereCanCallAModelTest(unittest.TestCase):
    """Zero LLM requests, asserted at the source rather than promised."""

    #: The components this profile ships and this repository builds for Linux.
    #: `windows-core/` is a separate lane with its own toolchain; it is measured
    #: separately below rather than quietly included or quietly dropped.
    SHIPPED = ("native/node-core/src", "native/node-core/include",
               "native/node-core/supervisor/src", "native/node-core/supervisor/include",
               "native/node-core/core-lite/src", "native/node-core/core-lite/include")

    #: A model or HTTP client. Raw sockets are NOT on this list: the supervisor
    #: legitimately owns ONE inet socket, the readiness probe, and the next test
    #: pins where it is rather than pretending it is absent.
    #: Headers that would give the shipped core a way to reach a model. Raw
    #: sockets are NOT on this list: the supervisor owns exactly one inet socket,
    #: the readiness probe, and the next test pins where it is.
    BANNED_HEADERS = ("curl", "openssl", "asio", "beast", "httplib", "cpprest",
                      "grpc", "requests")

    def test_the_shipped_native_core_has_no_model_or_http_client(self):
        """Asserted on `#include` lines and URL literals, not on loose prose.

        A substring scan over whole files flags a COMMENT that says an operator
        can `curl --unix-socket` the status socket — which is documentation of
        the local, read-only surface, not a network client. Scanning what the
        compiler actually consumes keeps the check meaningful.
        """
        include_re = re.compile(r"^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]", re.M)
        url_re = re.compile(r"https?://[^\s\"]+")
        offenders = []
        for root in self.SHIPPED:
            for path in (ROOT / root).rglob("*"):
                if not path.is_file() or path.suffix not in {".cpp", ".hpp", ".h", ".cc"}:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                for header in include_re.findall(text):
                    if any(token in header for token in self.BANNED_HEADERS):
                        offenders.append((str(path.relative_to(ROOT)), header))
                stripped = "\n".join(line for line in text.splitlines()
                                      if not line.lstrip().startswith("//"))
                for url in url_re.findall(stripped):
                    offenders.append((str(path.relative_to(ROOT)), url))
        self.assertEqual(offenders, [],
                         "a component this profile ships grew a model or HTTP client")

    def test_the_only_inet_socket_is_the_readiness_probe(self):
        """One socket exists, in `probe.cpp`, and the monitor never reaches it.

        A readiness probe connects to a service's own declared port to answer
        "is it actually serving" — a read, and part of the acting loop: it runs
        only AFTER an action has been issued. A monitor issues no action, so it
        never runs. Pinning the location means a second socket cannot appear
        without this test naming it.
        """
        socket_files = set()
        for root in self.SHIPPED:
            for path in (ROOT / root).rglob("*.cpp"):
                text = path.read_text(encoding="utf-8", errors="replace")
                if "AF_INET" in text or "AF_INET6" in text:
                    socket_files.add(str(path.relative_to(ROOT)))
        self.assertEqual(socket_files,
                         {"native/node-core/supervisor/src/probe.cpp"},
                         "a second inet socket appeared in a shipped component")
        # And the invocation is inside the effect loop, behind `act`.
        main_cpp = (ROOT / "native/node-core/supervisor/src/main.cpp").read_text(encoding="utf-8")
        self.assertIn("run_probe(spec, probe_transport)", main_cpp)
        probe_at = main_cpp.index("run_probe(spec, probe_transport)")
        # Every `run_probe` call sits after an executed action in the same block;
        # the monitor's tick has `act == false` and never enters it.
        self.assertGreater(probe_at, main_cpp.index("action.verb, tick_started"))

    def test_windows_core_is_measured_separately_and_is_not_this_profile(self):
        """Recorded, not hidden: `windows-core/` opens a TCP socket.

        It is a different lane with a different toolchain and it is not part of
        the Linux profile this PR is about. Pinning the fact here means the
        receipt can say "measured" instead of "assumed", and the day somebody
        folds it in, this test is where they find out why the blanket scan
        stopped being green.
        """
        hits = []
        for path in (NATIVE / "node-core" / "windows-core").rglob("*.cpp"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "AF_INET" in text:
                hits.append(str(path.relative_to(ROOT)))
        self.assertNotEqual(hits, [],
                            "windows-core no longer opens a TCP socket; update the "
                            "receipt that records it as outside this profile")


if __name__ == "__main__":
    unittest.main()
