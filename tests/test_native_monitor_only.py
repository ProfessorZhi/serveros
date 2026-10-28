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
    # `--owner-map` authorises the acting path. In the monitor it means nothing,
    # so it is refused with the rest rather than left to trip a later guard.
    ("--owner-map", "x.json"),
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
            # before. Asserting "exit 2" alone would pass even if the actuator
            # had been deleted — an unknown option also exits 2. So the acting
            # profile is driven end to end against a FAKE service manager and
            # required to record the verb.
            unit = "toy.service"
            calls = Path(td) / "calls.log"
            systemctl = Path(td) / "systemctl"
            systemctl.write_text(
                "#!/bin/sh\n"
                f'printf \'%s\\n\' "$*" >> "{calls}"\n'
                "for a in \"$@\"; do\n"
                "  case \"$a\" in\n"
                "    show) printf 'LoadState=loaded\\nActiveState=failed\\nSubState=failed\\n'; exit 0 ;;\n"
                "    restart|start|stop) exit 0 ;;\n"
                "  esac\n"
                "done\nexit 0\n", encoding="utf-8")
            systemctl.chmod(0o755)
            manifest.write_text(json.dumps({
                "schema": "serveros/desired-state.v1", "node": "n", "principal": "p",
                "observe_only": False, "generation": 1,
                "services": [{"id": "toy", "owner": "p", "unit": unit,
                              "desired": "running",
                              "recovery": {"allowed_verbs": ["restart"],
                                           "offline_recoverable": True}}],
            }), encoding="utf-8")
            owner_map = Path(td) / "map.json"
            owner_map.write_text(json.dumps({
                "schema": "serveros/native-core-owner-map.v1",
                "units": {unit: "native-core"}}), encoding="utf-8")
            proc = subprocess.run(
                [str(self.binary), "--manifest", str(manifest), "--allow-unit", unit,
                 "--enable-actions", "--owner-map", str(owner_map),
                 "--lease", str(Path(td) / "l"), "--state", str(Path(td) / "s.json"),
                 "--link-state", "up", "--agent-state", "idle",
                 "--systemctl", str(systemctl)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("restart", calls.read_text(encoding="utf-8"),
                          "the acting path no longer acts; the protected feature the "
                          "owner asked to keep has been lost")


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
            "    restart|start|stop) exit 0 ;;\n"
            "  esac\n"
            "done\nexit 0\n", encoding="utf-8")
        self.systemctl.chmod(0o755)
        (self.dir / "xdg" / "serveros").mkdir(parents=True)
        self.socket_path = self.dir / "s.sock"
        self.manifest = self.dir / "m.json"
        self.manifest.write_text(json.dumps({
            "schema": "serveros/desired-state.v1", "node": "test-node", "principal": "p",
            # MUST be false. With `observe_only: true` the planner clears every
            # action regardless of `act`, so "zero verbs" would be true of ANY
            # profile — the test would pass with `--monitor-only` removed. The
            # permission is granted here so that only the profile suppresses it.
            "observe_only": False, "generation": 1,
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
        # It OBSERVED — the unit was read and reported failed — and asked for
        # nothing else. A restart here would be the automatic healing the owner
        # deferred; `show` is the only thing a monitor may ask.
        self.assertTrue(all("show" in line for line in calls),
                        f"the monitor issued a verb: {calls[:5]}")
        # Nothing is printed per-service in the resident loop; the observation
        # is in the status document, and the next test reads it there.

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
        # The planner is allowed to see work — this manifest PERMITS acting, and
        # the unit is failed. What a monitor must not do is EXECUTE it. A plan of
        # 1 with a fake log that contains no verb is the boundary, stated.
        self.assertEqual(doc["plan"]["actions"], 1,
                         "the planner stopped planning; this test no longer distinguishes "
                         "planning from executing")
        calls = self.calls.read_text(encoding="utf-8") if self.calls.exists() else ""
        self.assertNotIn("restart", calls, "the monitor executed the plan it made")
        monitor = doc["monitor"]
        self.assertEqual(monitor["profile"], "monitor-only")
        # The cost is MEASURED. These are bounds on the real numbers, not a
        # restatement of a comment: 5 MB RSS and single-digit milliseconds are
        # what this host reported, with room for a slower machine.
        self.assertLess(monitor["rss_kb"], 64 * 1024, monitor)
        self.assertLess(monitor["cpu_ms_total"], 2000, monitor)
        self.assertLess(monitor["tick_ms"], 1000, monitor)
        self.assertGreaterEqual(doc["ticks"], 1)

    def test_the_monitor_is_resident_without_being_told_to_serve(self):
        """`--monitor-only` implies the resident loop; that is the profile."""
        proc = subprocess.run(self._argv("--max-ticks", "2"), env=self._env(),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ticks=2", proc.stdout + proc.stderr)


class TheShippedUnitStartsTheMonitorTest(unittest.TestCase):
    """A profile nothing selects is a flag, not a shipped shape.

    The first version of this PR added `--monitor-only` and left the unit
    template running `--serve`, so a shipped instance reported
    `profile: "serve"` and the owner's boundary existed only for an operator who
    happened to add the flag. The template is a DECLARATION, not an installation
    — nothing here installs it — but a declaration that names the wrong profile
    is the defect this test exists for.
    """

    TEMPLATE = ROOT / "native/node-core/supervisor/systemd/serveros-native-core.service"

    def test_the_template_starts_the_monitor_profile(self):
        text = self.TEMPLATE.read_text(encoding="utf-8")
        exec_lines = [line for line in text.splitlines() if line.startswith("ExecStart=")]
        self.assertEqual(len(exec_lines), 1, exec_lines)
        self.assertIn("--monitor-only", exec_lines[0])
        self.assertNotIn("--enable-actions", exec_lines[0])

    def test_the_template_is_still_declared_not_installed(self):
        text = self.TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("NOT INSTALLED", text)


class NothingHereCanCallAModelTest(unittest.TestCase):
    """Zero LLM requests, asserted against the ways a model client could arrive.

    THE LIMITS, STATED. This is a source guard, not a proof. It cannot see a
    binary that dlopens a library by a name it computes, and it says nothing
    about `--systemctl PATH`, which deliberately executes an operator-named
    program (`systemd_user.cpp` -> `proc.cpp`) because that is the provider seam.
    What it does hold is that the shipped sources contain no model client, no
    dynamic loader and no HTTP stack, and that the only process-execution call
    site is the one bounded runner this project already declares.
    """

    #: The components this profile ships and this repository builds for Linux.
    #: `windows-core/` is a separate lane with its own toolchain; it is measured
    #: separately below rather than quietly included or quietly dropped.
    SHIPPED = ("native/node-core/src", "native/node-core/include",
               "native/node-core/supervisor/src", "native/node-core/supervisor/include",
               "native/node-core/core-lite/src", "native/node-core/core-lite/include")

    #: Headers that would give the shipped core a way to reach a model.
    BANNED_HEADERS = ("curl", "openssl", "asio", "beast", "httplib", "cpprest",
                      "grpc", "requests")
    #: Ways to acquire a client without an `#include`.
    BANNED_CALLS = ("dlopen", "dlsym", "dlerror")

    def _sources(self):
        for root in self.SHIPPED:
            for path in (ROOT / root).rglob("*"):
                if path.is_file() and path.suffix in {".cpp", ".hpp", ".h", ".cc", ".c", ".cxx"}:
                    yield path

    @staticmethod
    def _code_only(text: str) -> str:
        return "\n".join(line for line in text.splitlines()
                          if not line.lstrip().startswith("//"))

    def test_no_model_or_http_client_header(self):
        include_re = re.compile(r"^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]", re.M)
        url_re = re.compile(r"https?://[^\s\"]+")
        offenders = []
        for path in self._sources():
            text = path.read_text(encoding="utf-8", errors="replace")
            for header in include_re.findall(text):
                if any(token in header for token in self.BANNED_HEADERS):
                    offenders.append((str(path.relative_to(ROOT)), header))
            for url in url_re.findall(self._code_only(text)):
                offenders.append((str(path.relative_to(ROOT)), url))
        self.assertEqual(offenders, [],
                         "a component this profile ships grew a model or HTTP client")

    def test_nothing_loads_code_at_runtime(self):
        """`dlopen` would make the header scan meaningless, so it is refused."""
        offenders = []
        for path in self._sources():
            code = self._code_only(path.read_text(encoding="utf-8", errors="replace"))
            for token in self.BANNED_CALLS:
                if token in code:
                    offenders.append((str(path.relative_to(ROOT)), token))
        self.assertEqual(offenders, [])

    def test_the_only_process_execution_is_the_bounded_runner(self):
        """One `execvp`, in `proc.cpp`, which is the declared seam.

        A second exec site would be a second way to run something, and the point
        of the bounded runner is that there is exactly one.
        """
        call_re = re.compile(r"\b(execvp|execv|execve|popen|system)\s*\(")
        sites = []
        for path in self._sources():
            code = self._code_only(path.read_text(encoding="utf-8", errors="replace"))
            for match in call_re.finditer(code):
                sites.append((str(path.relative_to(ROOT)), match.group(1)))
        self.assertEqual(sorted(set(path for path, _ in sites)),
                         ["native/node-core/supervisor/src/proc.cpp"],
                         f"a second process-execution site appeared: {sites}")

    def test_the_only_inet_socket_is_the_readiness_probe(self):
        """One socket exists, in `probe.cpp`, and the monitor never reaches it.

        The reachability half is proven by the fake service manager in
        `TheMonitorDoesNothingTest` — a fake log containing only `show` is a
        measurement, whereas the position of a call in a file is a coincidence
        of layout. What is asserted here is the SET: one file, one family, and a
        single `run_probe` call site.
        """
        socket_re = re.compile(r"\b(?:AF|PF)_INET6?\b")
        socket_files = set()
        for path in self._sources():
            code = self._code_only(path.read_text(encoding="utf-8", errors="replace"))
            if socket_re.search(code):
                socket_files.add(str(path.relative_to(ROOT)))
        self.assertEqual(socket_files,
                         {"native/node-core/supervisor/src/probe.cpp"},
                         "a second inet socket appeared in a shipped component")

        main_cpp = (ROOT / "native/node-core/supervisor/src/main.cpp").read_text(encoding="utf-8")
        self.assertEqual(main_cpp.count("run_probe("), 1,
                         "a second readiness-probe call site appeared")

    def test_windows_core_is_measured_separately_and_is_not_this_profile(self):
        """Recorded, not hidden: `windows-core/` opens a TCP socket.

        It is a different lane with a different toolchain and it is not part of
        the Linux profile this PR is about. Pinning the fact here means the
        receipt can say "measured" instead of "assumed".
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
