#!/usr/bin/env python3
"""Where a credential could get out, and the tests that say it does not.

Issue #152 is a gate in front of expanding Fleet's authority: a production
configuration check had already put a CloudCLI provider credential, a JWT
signing key and a browser-MCP token somewhere an Agent reads. This file is the
regression suite for the paths that exist *here*, and it is written so that it
never needs a real value to be meaningful.

Two kinds of test, deliberately:

*   **Behavioural.** A real HTTP server, on loopback, answering the way a
    gateway answers when it is unhappy — a 500 with a body, a 200 that is not
    JSON, a JSON-RPC error object, a tool result flagged `isError`. Each one
    quotes a freshly generated credential back at the client, which is what a
    provider SDK does with the key it was configured with. The assertion is not
    "the credential was masked"; it is "the credential did not cross", because
    a mask can be wrong and a withheld string cannot.
*   **Source-level.** The unit files and install scripts are read as text and
    checked for the shapes that put a value into a place that outlives the
    process: a `Description=`, an `Environment=`, an `ExecStart=`, a scheduled
    task's `-Argument`. These are asserted against the repository, so a future
    edit that adds `Environment=CLOUDCLI_PAT=...` fails here rather than in a
    transcript.

Fixtures are generated per run. The credential shapes that are assembled from
pieces are assembled at runtime, exactly as `tests/test_fleet_mcp_identity.py`
does, so that a scanner reading this file finds no credential in it.
"""
from __future__ import annotations

import http.server
import json
import re
import secrets
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import disclosure, privacy  # noqa: E402
from fleet_mcp.config import ProviderBinding  # noqa: E402
from fleet_mcp.errors import FleetRefusal  # noqa: E402
from fleet_mcp.mcp_http import McpHttpClient, ProviderRefusal, structured  # noqa: E402
from fleet_mcp.providers.cloudcli import CloudCliProvider  # noqa: E402

FLEET_MCP_DIR = ROOT / "fleet_mcp"


def a_secret() -> str:
    """A value worth not leaking, freshly generated per call."""
    return secrets.token_hex(24)


# -- a gateway that quotes its own configuration back ----------------------


class ScriptedGateway(http.server.BaseHTTPRequestHandler):
    """One response, scripted, including the body a real gateway would send.

    A provider SDK handed a bad key puts the key in the error it raises, and a
    gateway in front of it passes the error along. That is not a hypothetical:
    it is the ordinary way an upstream credential reaches a log, and the body
    below is how one arrives here.
    """

    status = 500
    content_type = "application/json"
    body = ""
    scripted: list[tuple[int, str, str]] = []

    def do_POST(self) -> None:  # noqa: N802 - http.server's spelling
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if type(self).scripted:
            status, ctype, body = type(self).scripted.pop(0)
        else:
            status, ctype, body = type(self).status, type(self).content_type, type(self).body
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:
        """Silent. A server that logs the request would log the header."""


class GatewayOnLoopback(unittest.TestCase):
    """A real socket, because the thing under test is the real client."""

    def setUp(self):
        self.secret = a_secret()
        ScriptedGateway.scripted = []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ScriptedGateway)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        # Cleanups run last-registered-first, so this reads in reverse: the
        # loop is stopped, the socket is released, and only then is the thread
        # joined. Closing the socket first leaves `serve_forever` selecting on
        # a descriptor that no longer exists, which is an exception in another
        # thread that pytest attributes to whatever test runs next.
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.endpoint = f"http://127.0.0.1:{self.server.server_address[1]}/mcp"

    def script(self, status: int, content_type: str, body: str) -> None:
        ScriptedGateway.scripted.append((status, content_type, body))

    def client(self) -> McpHttpClient:
        return McpHttpClient(self.endpoint, None, read_timeout=10.0, label="CloudCLI on device:test")

    def assert_is_clean(self, text: str) -> None:
        self.assertNotIn(self.secret, text)
        for fragment in re.findall(r"[A-Za-z0-9_\-]{16,}", self.secret):
            self.assertNotIn(fragment, text)

    # -- the four shapes a provider failure takes --------------------------

    def test_an_error_body_is_not_relayed(self):
        """A 500 whose body quotes the upstream key back at us."""
        body = json.dumps(
            {"error": {"message": f"invalid x-api-key: {self.secret}", "type": "authentication_error"}}
        )
        self.script(500, "application/json", body)
        with self.assertRaises(ProviderRefusal) as caught:
            self.client().initialize()
        self.assert_is_clean(str(caught.exception))
        self.assert_is_clean(caught.exception.detail)
        self.assertIn("HTTP 500", caught.exception.detail)
        self.assertIn(f"{len(body)} bytes", caught.exception.detail)

    def test_a_response_that_is_not_json_is_not_relayed(self):
        """Something in front of the gateway, answering for it."""
        self.script(200, "text/html", f"<html><body>502 {self.secret}</body></html>")
        with self.assertRaises(ProviderRefusal) as caught:
            self.client().initialize()
        self.assert_is_clean(str(caught.exception))
        self.assertIn("html", caught.exception.detail)

    def test_a_json_rpc_error_message_is_not_relayed(self):
        """The reply is well-formed and the message inside it is not ours."""
        self.script(
            200,
            "application/json",
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32603, "message": f"upstream rejected {self.secret}"},
                }
            ),
        )
        with self.assertRaises(ProviderRefusal) as caught:
            self.client().initialize()
        self.assert_is_clean(str(caught.exception))
        self.assertIn("-32603", caught.exception.detail)

    def test_a_tool_result_flagged_as_an_error_is_not_relayed(self):
        """`structured()` is the last place a remote message could cross."""
        result = {
            "isError": True,
            "content": [{"type": "text", "text": f"configuration is invalid at {self.secret}"}],
        }
        with self.assertRaises(FleetRefusal) as caught:
            structured(result)
        self.assert_is_clean(str(caught.exception))
        self.assert_is_clean(caught.exception.detail)

    def test_an_error_with_no_text_says_what_it_can(self):
        with self.assertRaises(FleetRefusal) as caught:
            structured({"isError": True, "content": []})
        self.assertIn("said nothing more", caught.exception.detail)

    # -- the same thing, through the surface the credential actually reads --

    def test_the_provider_refusal_carries_nothing_remote(self):
        """`CloudCliProvider._translate` passes a detail through unchanged.

        That passthrough is correct once the detail is written here rather than
        by the far end, and the two tests below are why the distinction matters:
        this is the path a refusal takes into a tool result, which is the path
        into an Agent's transcript.
        """
        self.script(500, "application/json", json.dumps({"detail": self.secret}))
        provider = self._provider()
        with self.assertRaises(FleetRefusal) as caught:
            provider.list_sessions(limit=10)
        self.assert_is_clean(str(caught.exception))
        self.assert_is_clean(caught.exception.detail)

    def test_the_health_detail_carries_nothing_remote(self):
        """`fleet_status` returns provider health, detail and all."""
        self.script(500, "text/plain", f"gateway config: {self.secret}")
        health = self._provider().health()
        self.assertFalse(health.authorized is None)  # a health object, not a crash
        self.assert_is_clean(json.dumps(health.as_dict()))
        self.assert_is_clean(health.detail)

    def _provider(self) -> CloudCliProvider:
        tmp = Path(tempfile.mkdtemp(prefix="fleet-hygiene-"))
        return CloudCliProvider(
            ProviderBinding(
                device_ref="device:test",
                kind="cloudcli-mcp-http",
                endpoint=self.endpoint,
                # An anchor that does not exist: no credential is read, sent or
                # needed. The test is about what comes back, not what goes out.
                credential_anchor=tmp / "no-such-credential",
            )
        )


class TheOldShapesCannotComeBack(unittest.TestCase):
    """The three expressions that used to relay remote text, asserted absent.

    A behavioural test proves today's paths are clean. This one is what stops
    the shape from returning in a branch that no test drives: the whole point
    of `raw[:200]` was that it looked like a bound.
    """

    def setUp(self):
        self.source = (FLEET_MCP_DIR / "mcp_http.py").read_text(encoding="utf-8")

    def test_no_remote_body_is_ever_sliced(self):
        self.assertNotIn("raw[", self.source)

    def test_the_summary_is_what_carries_failure_detail(self):
        self.assertIn("remote_summary(", self.source)
        self.assertIn("from .disclosure import remote_summary", self.source)

    def test_no_upstream_error_message_is_interpolated(self):
        for shape in ("err.get('message')", 'err.get("message")', "err.get('code')"):
            self.assertNotIn(shape, self.source.replace("error_message=err.get", "error_message=Ok("))


class DisclosureHasNoEscapeHatch(unittest.TestCase):
    """The allowlist is the rule, so there is no way to ask it to relax."""

    def test_no_permissive_variant_exists(self):
        forbidden = ("scrub", "sanitize", "sanitise", "best_effort", "besteffort", "allow_all", "force")
        offenders = [
            name
            for name in dir(disclosure)
            if not name.startswith("_") and any(word in name.lower() for word in forbidden)
        ]
        self.assertEqual(offenders, [])

    def test_the_allowlist_cannot_be_widened_by_accident(self):
        """A frozenset, so an in-place `add` is an error rather than a silent
        change to every record the surface writes from then on."""
        self.assertIsInstance(disclosure.SAFE_AUDIT_FIELDS, frozenset)
        with self.assertRaises(AttributeError):
            disclosure.SAFE_AUDIT_FIELDS.add("authorization")  # type: ignore[attr-defined]

    def test_the_omission_is_visible_in_the_output(self):
        """A withheld field has to be countable, or the rule is a silent one."""
        self.assertTrue(disclosure.OMITTED_MARK)
        projected, withheld = disclosure.project_record({"event": "e", "nope": "x"})
        self.assertEqual(projected["withheld"], withheld)


# -- the places a value outlives the process -------------------------------

# Reviewed and complete: every placeholder in an `ExecStart=` or a unit's
# metadata resolves to a path, a host, a port, or the runtime root. A
# placeholder that resolves to a *value* would have to be added here first.
REVIEWED_PLACEHOLDERS = frozenset(
    {
        "@FLEET_MCP_RUNTIME@",
        "@FLEET_MCP_CONFIG@",
        "@REPO_ROOT@",
        "@BINDING@",
        "@SSH_HOST@",
        "@REMOTE_PORT@",
        "@LOCAL_PORT@",
        "@IDENTITY@",
        "@KNOWN_HOSTS@",
    }
)

# The placeholder names that carry a *path to* a secret rather than a secret.
ANCHOR_PLACEHOLDERS = frozenset({"@SECRET_ANCHOR@"})

UNIT_METADATA_KEYS = ("Description", "Documentation", "Environment", "EnvironmentFile",
                      "ExecStart", "ExecStartPre", "WorkingDirectory", "User")

SOUNDS_LIKE_A_SECRET = re.compile(r"(?i)(TOKEN|SECRET|PASSWORD|PASSWD|APIKEY|API_KEY|CREDENTIAL|PAT\b)")


def unit_files() -> list[Path]:
    found = sorted(FLEET_MCP_DIR.glob("deploy/*.service"))
    found += sorted(FLEET_MCP_DIR.glob("node/systemd/**/*.in"))
    found += sorted((ROOT / "runtime").glob("*.service"))
    return found


def unit_directives(path: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "[")):
            continue
        key, sep, value = line.partition("=")
        if sep and key.strip() in UNIT_METADATA_KEYS:
            out.append((key.strip(), value.strip()))
    return out


class SystemdUnitsCarryNoCredentials(unittest.TestCase):
    """A unit's metadata is copied into scope, listed, and read by anything
    that asks what is running. It is the last place to put a value."""

    def test_there_are_units_to_check(self):
        self.assertGreaterEqual(len(unit_files()), 5)

    def test_no_unit_metadata_line_holds_a_credential_shape(self):
        for path in unit_files():
            for key, value in unit_directives(path):
                with self.subTest(unit=path.name, key=key):
                    cleaned, hits = privacy.redact_text(value)
                    self.assertEqual(hits, 0, f"{path.name}: {key}= looks like a credential: {cleaned}")

    def test_no_unit_sets_an_environment_variable_that_names_a_secret(self):
        for path in unit_files():
            for key, value in unit_directives(path):
                if key != "Environment":
                    continue
                name = value.split("=", 1)[0]
                with self.subTest(unit=path.name, name=name):
                    self.assertIsNone(
                        SOUNDS_LIKE_A_SECRET.search(name),
                        f"{path.name} sets {name}: a variable that names a secret "
                        f"is a variable somebody will eventually print",
                    )

    def test_an_environment_file_is_an_absolute_path_and_not_a_value(self):
        """`EnvironmentFile=` names a file whose permissions are the control.
        It must never be a path with the value in it, and it must be readable
        by the unit rather than by anybody."""
        for path in unit_files():
            for key, value in unit_directives(path):
                if key != "EnvironmentFile":
                    continue
                target = value.lstrip("-")
                with self.subTest(unit=path.name, target=target):
                    self.assertTrue(
                        target.startswith(("/", "%h/", "%t/")),
                        f"{path.name} points EnvironmentFile at {target!r}, which is "
                        f"not an absolute path",
                    )

    def test_fleet_exec_starts_reference_only_reviewed_placeholders(self):
        fleet_units = sorted(FLEET_MCP_DIR.glob("deploy/*.service"))
        fleet_units += sorted(FLEET_MCP_DIR.glob("node/systemd/**/*.in"))
        for path in fleet_units:
            for key, value in unit_directives(path):
                for placeholder in re.findall(r"@[A-Z0-9_]+@", value):
                    with self.subTest(unit=path.name, placeholder=placeholder):
                        self.assertIn(placeholder, REVIEWED_PLACEHOLDERS)

    def test_no_fleet_unit_names_the_secret_anchor_in_its_metadata(self):
        """The binding is referenced by path; the thing the binding anchors is
        not referenced at all."""
        for path in sorted(FLEET_MCP_DIR.glob("node/systemd/**/*.in")):
            for key, value in unit_directives(path):
                with self.subTest(unit=path.name, key=key):
                    self.assertNotIn("@SECRET_ANCHOR@", value)


class InstallScriptsPutNothingSecretInAnArgument(unittest.TestCase):
    """What an install script hands to a launcher is visible to every user on
    the machine through the process list, and to anything that reads a
    scheduled task's definition."""

    def setUp(self):
        self.sh = (FLEET_MCP_DIR / "node/install.sh").read_text(encoding="utf-8")
        self.ps1 = (FLEET_MCP_DIR / "node/install.ps1").read_text(encoding="utf-8")

    def test_the_secret_files_contents_are_only_measured_hashed_or_located(self):
        """The load-bearing rule of this installer, stated as code.

        A file whose contents are only ever counted, hashed, or have their
        directory resolved cannot be printed by accident later, because nothing
        ever holds the contents. Each command below yields a length, a digest,
        or a path; none of them can yield the file's bytes, which is the
        property being asserted rather than a list of preferred tools.
        """
        substitutions = re.findall(r"\$\(([^()]*(?:\([^()]*\)[^()]*)*)\)", self.sh)
        touching = [inner.strip() for inner in substitutions if "SECRET" in inner]
        self.assertTrue(touching, "no substitution reads the secret — the scan is broken")
        reviewed = ("wc -c", "sha256sum", "dirname")
        for inner in touching:
            with self.subTest(substitution=inner):
                self.assertTrue(
                    any(inner.startswith(reader) for reader in reviewed),
                    f"the secret file is read by {inner!r}, which is not one of the "
                    f"reviewed uses ({', '.join(reviewed)})",
                )

    def test_the_secret_file_is_never_sourced_or_read_into_the_environment(self):
        self.assertNotRegex(self.sh, r"\$\(<")
        self.assertNotRegex(self.sh, r"(?m)^\s*read\b[^\n]*SECRET")
        self.assertNotRegex(self.sh, r"(?m)^\s*(export|declare|source|\.)\s[^\n]*SECRET_PATH")

    def test_the_only_secret_derived_names_ever_printed_are_paths_and_lengths(self):
        printed: set[str] = set()
        for line in self.sh.splitlines():
            if not re.search(r"\b(say|warn|printf|echo)\b", line):
                continue
            printed |= set(re.findall(r"\$\{?(SECRET[A-Z_]*)\}?", line))
        self.assertTrue(printed, "the scan found no printed secret-derived name")
        self.assertLessEqual(printed, {"SECRET_PATH", "SECRET_LEN"}, printed)
        # And what is printed *about* the secret is its fingerprint, which is
        # what makes "neither end had to be told the value" checkable.
        self.assertIn("FINGERPRINT=$(sha256sum", self.sh)

    def test_the_windows_secret_variable_is_only_assigned_and_written(self):
        uses = [
            line.strip()
            for line in self.ps1.splitlines()
            if "$secret" in line and not line.strip().startswith("#")
        ]
        self.assertTrue(uses, "no `$secret` assignment was found — the scan is broken")
        for line in uses:
            with self.subTest(line=line):
                self.assertRegex(line, r"(\$secret\s*=[^=]|WriteAllText)")

    def test_the_shell_installer_substitutes_only_paths_hosts_and_ports(self):
        """`render_unit` is the one place a value becomes a unit file, so its
        substitution map is the whole surface to review."""
        substitutions = dict(
            re.findall(r's\|(@[A-Z0-9_]+@)\|\$([A-Z_]+)\|g', self.sh)
        )
        self.assertTrue(substitutions, "the substitution map was not found — the scan is broken")
        allowed_sources = {
            "REPO_ROOT", "BINDING_FILE", "SECRET_PATH", "LOG_DIR", "STATE_ROOT",
            "SSH_HOST", "REMOTE_PORT", "LOCAL_PORT", "IDENTITY", "KNOWN_HOSTS",
        }
        for placeholder, source in substitutions.items():
            with self.subTest(placeholder=placeholder):
                self.assertIn(source, allowed_sources)
                if "SECRET" in source:
                    self.assertIn(
                        placeholder,
                        ANCHOR_PLACEHOLDERS,
                        f"{placeholder} would put the secret itself into a unit",
                    )

    def test_the_windows_installer_registers_tasks_without_credentials(self):
        for array in ("$connectorArgs", "$tunnelArgs"):
            body = self._assignment_body(array)
            with self.subTest(array=array):
                for variable in ("$secret", "$SecretPath", "$CloudCliCredential", "$fingerprint"):
                    self.assertNotIn(variable, body)

    def test_the_windows_installer_hands_the_task_a_script_and_its_arguments(self):
        self.assertIn(
            "$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argument",
            self.ps1,
        )
        self.assertIn("$ScriptPath", self._assignment_body("$argument"))

    def test_the_windows_installer_prints_a_fingerprint_and_not_the_secret(self):
        self.assertIn("Get-FileHash", self.ps1)
        for line in self.ps1.splitlines():
            if not re.search(r"Write-(Host|Warning|Output|Verbose)", line):
                continue
            with self.subTest(line=line.strip()):
                self.assertNotIn("$secret", line)
        self.assertIn("secret fingerprint", self.ps1)

    def test_the_launchers_are_given_a_binding_path_and_nothing_else(self):
        for name in ("node/start-connector.ps1", "node/start-connector.sh", "node/start-tunnel.ps1",
                     "node/start-tunnel.sh"):
            source = (FLEET_MCP_DIR / name).read_text(encoding="utf-8")
            with self.subTest(launcher=name):
                for variable in ("secret_anchor", "SECRET_PATH", "$secret", "credential"):
                    self.assertNotIn(variable, source)

    def _assignment_body(self, array: str) -> str:
        """Every line that builds or extends one argument array.

        PowerShell's continuation is a trailing backtick and an array literal
        extends to its closing paren, so neither "the next line" nor "until a
        blank line" is the boundary. Both are tracked, and every statement that
        mentions the array is collected — the array is built by assignment and
        then extended, and a check that only read the first line would pass
        while missing exactly the line that mattered.
        """
        lines = self.ps1.splitlines()
        out: list[str] = []
        index = 0
        while index < len(lines):
            if array not in lines[index]:
                index += 1
                continue
            depth = 0
            while index < len(lines):
                line = lines[index]
                out.append(line)
                depth += line.count("(") - line.count(")")
                index += 1
                if not line.rstrip().endswith("`") and depth <= 0:
                    break
        return "\n".join(out)


class TheBindingKeepsAnchorsRatherThanValues(unittest.TestCase):
    """The one file this surface writes on a device holds paths."""

    def test_the_written_binding_names_anchors(self):
        for source in (
            (FLEET_MCP_DIR / "node/install.sh").read_text(encoding="utf-8"),
            (FLEET_MCP_DIR / "node/install.ps1").read_text(encoding="utf-8"),
        ):
            self.assertIn("secret_anchor", source)
            self.assertIn("cloudcli_credential_anchor", source)
            # The binding is a YAML document written here; no key of it is a
            # value, and a key named `secret` rather than `secret_anchor` is
            # how a copy gets made by accident.
            self.assertNotIn("\nsecret:", source)
            self.assertNotIn("\ncloudcli_credential:", source)


if __name__ == "__main__":
    unittest.main()
