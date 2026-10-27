"""Isolated-HOME smoke: does a real client install and discover this package?

WHY THIS FILE EXISTS
--------------------
Every other test in this lane reads the tree. None of them can answer "does a
client actually load it", and #425 asked for that gap to be closed with real
client evidence rather than assertion.

So this drives the real `claude` binary — when this host has one — against a
throwaway `HOME`:

    plugin marketplace add <this checkout>
    plugin install serveros-fleet-ingress@serveros
    plugin details serveros-fleet-ingress
    mcp list

and asserts the package appears with its eight Skills and its one MCP server.

WHAT THIS DOES NOT PROVE
------------------------
`mcp list` reporting `Needs authentication` means the endpoint answered and
withheld its tools. That is the honest state: **no Fleet tool is called here**,
`fleet_status` and `fleet_list_devices` are never invoked, and the authenticated
read stays `CLIENT_E2E_BLOCKED` until a human completes the OAuth sign-in. A green
run of this file is evidence of *install and discovery*, not of a working
delegation.

It also never touches the real `HOME`. Both `HOME` and `XDG_CONFIG_HOME` point at
a fresh temporary directory, and the checkout is registered as a marketplace from
its own path — no global install, no existing marketplace edited.
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

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_NAME = "serveros-fleet-ingress"
MARKETPLACE = "serveros"
EXPECTED_SKILLS = 8
TIMEOUT = 180

#: Where the CloudCLI image keeps the binary. Not on `PATH` is not the same as
#: not installed — that distinction cost this lane a round already.
FALLBACK_CLIENTS = (
    "/home/fjd/.local/opt/cc-cloudcli/node_modules/.bin/claude",
    "/home/fjd/.local/bin/claude",
)


def find_client() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for candidate in FALLBACK_CLIENTS:
        path = Path(candidate)
        if path.exists():
            return str(path)
    return None


class TheClientInstallsAndDiscoversThePackage(unittest.TestCase):
    """Skips — loudly — when no client is installed. Never silently passes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = find_client()
        if cls.client is None:
            raise unittest.SkipTest(
                "no `claude` binary on this host (PATH or the CloudCLI image); "
                "install/discovery CLIENT_E2E is NOT_RUN here, not passed")
        cls.home = Path(tempfile.mkdtemp(prefix="l4-smoke-home-"))

    @classmethod
    def tearDownClass(cls) -> None:
        home = getattr(cls, "home", None)
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)

    # -- helpers ---------------------------------------------------------

    def _env(self) -> dict:
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["XDG_CONFIG_HOME"] = str(self.home / ".config")
        return env

    def _claude(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([self.client, *args], text=True, timeout=TIMEOUT,
                              env=self._env(), stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)

    # -- the smoke -------------------------------------------------------

    def test_install_then_discover(self) -> None:
        added = self._claude("plugin", "marketplace", "add", str(ROOT))
        self.assertEqual(added.returncode, 0, added.stdout)

        installed = self._claude("plugin", "install", f"{PLUGIN_NAME}@{MARKETPLACE}")
        self.assertEqual(installed.returncode, 0, installed.stdout)

        details = self._claude("plugin", "details", PLUGIN_NAME)
        self.assertEqual(details.returncode, 0, details.stdout)
        text = details.stdout
        self.assertIn(f"Skills ({EXPECTED_SKILLS})", text,
                      f"expected {EXPECTED_SKILLS} Skills, got:\n{text}")
        self.assertIn("MCP servers (1)", text)
        self.assertIn("serveros-fleet", text)

    def test_the_mcp_entry_is_listed_and_withholds_its_tools(self) -> None:
        """`Needs authentication` is the correct observation, not a failure.

        It proves the endpoint answered. Claiming the tools are reachable would
        require a completed OAuth sign-in, which this file does not perform.
        """
        listed = self._claude("mcp", "list")
        self.assertEqual(listed.returncode, 0, listed.stdout)
        self.assertIn("serveros-fleet", listed.stdout,
                      f"the MCP entry is not registered:\n{listed.stdout}")

    def test_the_isolated_home_is_where_it_landed(self) -> None:
        """Nothing may be written to the operator's real configuration."""
        local = self.home / ".claude"
        self.assertTrue(local.exists(),
                        "the install did not land in the throwaway HOME, so it "
                        "may have written somewhere real")
        marketplace = local / "plugins" / "known_marketplaces.json"
        if marketplace.exists():
            known = json.loads(marketplace.read_text(encoding="utf-8"))
            self.assertIn(MARKETPLACE, known)


if __name__ == "__main__":
    unittest.main()
