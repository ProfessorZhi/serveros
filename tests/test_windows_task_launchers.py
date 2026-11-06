#!/usr/bin/env python3
"""The two scheduled tasks this repository registers on Windows must not
flash a console window, and must still let Task Scheduler see an exit code.

Both properties come from the same thing: the action is wscript.exe running
run-hidden-wait.vbs, not PowerShell itself.

*   **No window.** A task whose action is `powershell.exe` or `pwsh.exe` shows
    a console on every start, -WindowStyle Hidden notwithstanding: Windows
    allocates the console before PowerShell gets control, so by the time the
    script can hide itself the window has already appeared. For the Core-Lite
    publisher that is once a minute, for the Fleet connector at every logon.
    wscript.exe is a GUI-subsystem binary and allocates no console at all.
*   **A real exit code.** The launcher waits (`Run(command, 0, True)`) and
    returns the child's code, so -MultipleInstances IgnoreNew and the
    restart-on-failure settings still mean something. A fire-and-forget
    wrapper would report success the moment it started.

Source-level and offline, like the rest of this suite: the scripts are read as
text, and PowerShell is never executed. See
docs/fleet-reverse-tunnel-recovery.md, "Windows process lifetime".
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (registration script, the launcher it must point at, its own directory)
REGISTRATIONS = {
    "fleet connector": ROOT / "fleet_mcp/node/install.ps1",
    "core-lite publisher": ROOT / "native/node-core/core-lite/windows/deploy/register-publish-task.ps1",
}


class NoRegistrationStartsAConsole(unittest.TestCase):
    def test_no_registration_names_a_console_program_as_the_action(self):
        for name, path in REGISTRATIONS.items():
            source = path.read_text(encoding="utf-8")
            with self.subTest(task=name):
                self.assertIn(
                    "New-ScheduledTaskAction -Execute 'wscript.exe'", source
                )
                for console in ("-Execute 'powershell.exe'", "-Execute 'pwsh.exe'",
                                "-Execute $pwsh"):
                    self.assertNotIn(console, source)
                # The action's argument builds the launch. -WindowStyle Hidden
                # is the flag that looks like it works and does not, and -File
                # belongs to the launcher: the script is handed to it as an
                # argument, not flagged for PowerShell here.
                builds = [line for line in source.splitlines()
                          if re.match(r"\s*\$arguments?\s*=", line)]
                self.assertTrue(builds, "the task argument was not found")
                for line in builds:
                    with self.subTest(argument=line.strip()):
                        self.assertNotIn("-WindowStyle", line)
                        self.assertNotIn("-File", line)

    def test_each_registration_points_at_the_launcher_beside_it(self):
        for name, path in REGISTRATIONS.items():
            source = path.read_text(encoding="utf-8")
            with self.subTest(task=name):
                self.assertIn("run-hidden-wait.vbs", source)
                self.assertIn("$PSScriptRoot", source)
                launcher = path.parent / "run-hidden-wait.vbs"
                self.assertTrue(launcher.is_file(), f"{launcher} is missing")
                # The launcher has to be the bundled one, not a path elsewhere
                # in the tree: each deploy kit is shipped on its own.
                self.assertIn("Join-Path $PSScriptRoot 'run-hidden-wait.vbs'", source)


class TheLauncherWaitsAndReturnsTheExitCode(unittest.TestCase):
    def test_the_launcher_runs_hidden_waits_and_propagates_the_code(self):
        launchers = {path.parent.name: path.with_name("run-hidden-wait.vbs")
                     for path in REGISTRATIONS.values()}
        for owner, path in launchers.items():
            source = path.read_text(encoding="utf-8")
            with self.subTest(launcher=str(path)):
                # 0 = hidden window, True = wait for the child
                self.assertRegex(source, r"\.Run\(command,\s*0,\s*True\)")
                self.assertIn("WScript.Quit result", source)


if __name__ == "__main__":
    unittest.main()
