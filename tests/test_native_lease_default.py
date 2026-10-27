"""The lease path: one pen by construction, not by an operator remembering.

`runtime/daemon.py` says, in its own words, that a Core started with "the default
`--lease`" contends with it "by construction". For as long as this binary had no
default, that sentence was false: the two programs flocked the same file only
when somebody passed the daemon's path to the Core by hand. It has a default now,
derived exactly as the daemon derives its own, and this file holds the two
together.

The pure half compares the two sources that name the path. The binary half proves
the derivation — that the Core refuses to guess without `$XDG_RUNTIME_DIR`, and
that with one it takes the lease where the daemon would.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAEMON = ROOT / "runtime" / "native_core_facts.py"
DAEMON_MAIN = ROOT / "runtime" / "daemon.py"
CORE_MAIN = ROOT / "native/node-core/supervisor/src/main.cpp"

CANONICAL = "serveros/native-core.lease"


class TheSourcesAgreeTest(unittest.TestCase):
    def test_the_daemon_still_defaults_the_canonical_path(self):
        text = DAEMON_MAIN.read_text(encoding="utf-8")
        self.assertIn('"serveros"', text)
        self.assertIn('"native-core.lease"', text,
                      "the daemon no longer defaults this filename; the C++ default "
                      "now points at a file nobody else uses")

    def test_the_core_derives_the_same_path(self):
        text = CORE_MAIN.read_text(encoding="utf-8")
        self.assertIn(f'"/{CANONICAL}"', text,
                      "the C++ default and the daemon's default have drifted apart")

    def test_the_core_refuses_to_default_without_a_runtime_directory(self):
        text = CORE_MAIN.read_text(encoding="utf-8")
        # The refusal is the part that matters: a default invented from nothing
        # is a lease at a path no other program knows, which is the split-lock
        # the lease exists to prevent.
        self.assertIn("XDG_RUNTIME_DIR", text)
        self.assertIn("no default", text)


class TheCoreTakesItThereTest(unittest.TestCase):
    BINARY_ENV = "SERVEROS_NATIVE_CORE_BIN"

    def setUp(self):
        raw = os.environ.get(self.BINARY_ENV)
        if not raw or not Path(raw).is_file():
            self.skipTest(f"no native binary; set {self.BINARY_ENV}")
        self.binary = Path(raw)
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _fixtures(self, runtime_dir: Path) -> list[str]:
        (runtime_dir / "serveros").mkdir(parents=True, exist_ok=True)
        manifest = self.dir / "manifest.json"
        manifest.write_text(json.dumps({
            "schema": "serveros/desired-state.v1", "node": "n", "principal": "p",
            "observe_only": False, "generation": 1,
            "services": [{"id": "s", "owner": "p", "unit": "s.service",
                          "desired": "running", "start_on_boot": False,
                          "recovery": {"allowed_verbs": ["restart"],
                                       "offline_recoverable": True}}],
        }), encoding="utf-8")
        owner_map = self.dir / "map.json"
        owner_map.write_text(json.dumps({
            "schema": "serveros/native-core-owner-map.v1",
            "units": {"s.service": "native-core"}}), encoding="utf-8")
        facts = self.dir / "facts.json"
        facts.write_text(json.dumps({
            "schema": "serveros/native-core-facts.v1",
            "observed_at": "2026-10-28T00:00:00+00:00",
            "observed_epoch": int(time.time()),
            "link": {"known": True, "up": True},
            "agent": {"known": True, "busy": False}, "services": [],
        }), encoding="utf-8")
        systemctl = self.dir / "systemctl"
        systemctl.write_text(
            "#!/bin/sh\nfor a in \"$@\"; do\n"
            "  case \"$a\" in\n"
            "    show) printf 'LoadState=loaded\\nActiveState=failed\\nSubState=failed\\n'; exit 0 ;;\n"
            "    restart|start|stop) exit 0 ;;\n"
            "  esac\ndone\nexit 0\n", encoding="utf-8")
        systemctl.chmod(0o755)
        return [str(self.binary), "--manifest", str(manifest), "--allow-unit", "s.service",
                "--serve", "--enable-actions", "--owner-map", str(owner_map),
                "--facts", str(facts), "--state", str(self.dir / "recovery.json"),
                "--max-ticks", "1", "--interval-seconds", "1", "--no-status-socket",
                "--systemctl", str(systemctl)]

    def _run(self, args, *, runtime_dir):
        env = {**os.environ}
        env.pop("XDG_RUNTIME_DIR", None)
        if runtime_dir is not None:
            env["XDG_RUNTIME_DIR"] = str(runtime_dir)
        return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, env=env, timeout=60)

    def test_without_a_runtime_directory_the_core_refuses_rather_than_guesses(self):
        xdg = self.dir / "xdg"
        xdg.mkdir()
        proc = self._run(self._fixtures(xdg), runtime_dir=None)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("no default", proc.stderr)
        self.assertFalse((xdg / CANONICAL).exists())

    def test_the_lease_lands_where_the_daemon_would_put_it(self):
        xdg = self.dir / "xdg"
        proc = self._run(self._fixtures(xdg), runtime_dir=xdg)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((xdg / CANONICAL).exists(),
                        f"the Core did not take {CANONICAL}; the daemon's "
                        f"'by construction' would still be false")

    def test_an_explicit_lease_wins_and_the_canonical_path_stays_free(self):
        xdg = self.dir / "xdg"
        explicit = self.dir / "elsewhere.lease"
        args = self._fixtures(xdg)
        args += ["--lease", str(explicit)]
        proc = self._run(args, runtime_dir=xdg)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(explicit.exists(), "an explicit --lease was ignored")
        self.assertFalse((xdg / CANONICAL).exists(),
                         "the default was taken as well as the explicit path")


if __name__ == "__main__":
    unittest.main()
