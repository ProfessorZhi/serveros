#!/usr/bin/env python3
"""The launcher preflight: what it refuses, and what it must never say.

WHY THIS FILE EXISTS
--------------------
`runtime/launcher_guard.py` is a bound, and a bound is only a bound if it can
fire. The two ways a check like this stays green while being wrong are the
ordinary ones, and both are boring:

  * it fires on prose. "Bearer authentication" is how every document in this
    repository writes that phrase — including the header of the file this test
    is about — and a rule that refused it would refuse the wake that was
    quoting the guard. The negative set below is mostly that sentence.
  * it fires on nothing, because the rule it names is not the rule it runs.
    So the vocabulary is asserted to BE the vocabulary that two other modules
    already publish, rather than a second copy that agrees today.

The samples are assembled at runtime, for the reason `tests/test_secret_scan.py`
gives for doing the same: a file that spelled one out would be a
credential-shaped literal, in a repository whose release gate reports those.

The CLI is exercised as a subprocess rather than imported, because the CLI is
what a launcher calls and its exit code is what the launcher branches on. A test
that called `audit_argv_stream` directly would pass while the command was
broken — and the command reads its input from STDIN, which is itself a
property: a guard invoked as `guard <token>` would put the value it judges onto
a command line of its own.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "runtime"
GUARD = RUNTIME / "launcher_guard.py"
SCANNER = REPO / "scripts" / "scan_secrets.py"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


guard = _load("launcher_guard_under_test", GUARD)
shapes = _load("credential_shapes", RUNTIME / "credential_shapes.py")

#: One sample per value-shape rule. Each is assembled at runtime — the
#: private-key one included, and that one was caught: the first version of this
#: dict wrote the PEM header as a literal, and `scripts/scan_secrets.py`
#: reported this file. The right fix is to assemble it, not to add this file to
#: the scanner's `FIXTURE_PATHS`: that list is for files that must contain a
#: real-looking fixture, and this test does not have to.
SHAPE_SAMPLES = {
    "private-key": "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
    "github-token": "ghp_" + "A" * 36,
    "aws-access-key-id": "AKIA" + "B" * 16,
    "slack-token": "xoxb-" + "1" * 20,
    "anthropic-or-openai-key": "sk-" + "c" * 40,
}

#: The header form, which the release gate deliberately does not carry.
ARGV_ONLY_SAMPLES = {
    "bearer-credential": "Bearer " + "eyJhbGciOi.J9x-2Q.aBcDeFgHiJkLmNoP",
}

#: Text a command line is legitimately full of. None of it is a credential, and
#: a rule that fired on any of it is a rule somebody turns off. The first two
#: entries are the ones that matter: the phrase as English, and the phrase as
#: the name of a field.
NEGATIVE_SAMPLES = (
    "the Authorization header and Bearer authentication are discussed here",
    "Bearer tokens are how the client is configured",
    "sha256=7e6610ee9b044eecad2cd0ccd2c48ce9a2a56d985b84fc2a0439eb06ac9a0d2a",
    "path: /opt/applications/data/home-hwz/runtime-assets/hwz-server-os/current",
    "device_ref: device:server-beijing",
    "sk-learn is a python library",
    "resume SRV-0001",
    "mcpServers.continuation.env.SERVEROS_CONTINUATION_ENVELOPE",
    "--strict-mcp-config --mcp-config /tmp/x/mcp.json",
)


def run_guard(*tokens: str, mcp_config: Path | None = None) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(GUARD), "check-argv"]
    if mcp_config is not None:
        argv += ["--mcp-config", str(mcp_config)]
    return subprocess.run(argv, input=b"\0".join(t.encode() for t in tokens),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)


class EveryShapeFiresOnItsOwnSample(unittest.TestCase):
    """The positive half: a rule that cannot fire is not a rule."""

    def test_each_value_shape_is_found_in_its_own_sample(self):
        for rule, sample in SHAPE_SAMPLES.items():
            with self.subTest(rule=rule):
                self.assertIn(rule, shapes.shape_names_in(sample))

    def test_the_header_shape_is_found_only_when_argv_is_asked_for(self):
        """`argv=True` is the narrower question, and it is the one a line gets.

        The release gate answers the same call with the default and must NOT
        see a header: a release tree is allowed to document one.
        """
        sample = ARGV_ONLY_SAMPLES["bearer-credential"]
        self.assertEqual(shapes.shape_names_in(sample), [])
        self.assertEqual(shapes.shape_names_in(sample, argv=True), ["bearer-credential"])


class NoShapeFiresOnOrdinaryCommandLines(unittest.TestCase):
    """The negative half, and the half that decides whether this survives."""

    def test_nothing_in_the_negative_set_is_reported(self):
        for sample in NEGATIVE_SAMPLES:
            with self.subTest(sample=sample):
                self.assertEqual(shapes.shape_names_in(sample, argv=True), [])

    def test_a_clean_line_and_a_clean_config_are_not_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "mcp.json"
            config.write_text(json.dumps({"mcpServers": {"continuation": {
                "command": "/rev/mcp/bin/serveros-continuation-mcp",
                "env": {"SERVEROS_CONTINUATION_ENVELOPE": "/state/env.json"}}}}))
            proc = run_guard("--restricted", "--strict-mcp-config",
                             "--mcp-config", str(config), "-p", "resume SRV-0001",
                             mcp_config=config)
            self.assertEqual(proc.returncode, guard.CLEAN, proc.stderr)
            self.assertEqual(proc.stderr, b"")


class ForbiddenKeysAreFoundWhereverTheyNest(unittest.TestCase):
    """A key is a structural fact about a document, not a guess about its text."""

    def test_a_credential_key_is_found_four_levels_down(self):
        document = {"mcpServers": {"continuation": {
            "command": "/bin/true", "env": {"AUTHORIZATION": "…"}}}}
        self.assertEqual(shapes.key_paths_in(document),
                         ["mcpServers.continuation.env.AUTHORIZATION"])

    def test_the_lowercase_spelling_is_the_same_field(self):
        """`Authorization` and `authorization` are one field.

        A check that caught the spelling somebody happened to write is a check
        that passes review and misses the leak.
        """
        found = shapes.key_paths_in({"authorization": "…"})
        self.assertEqual(found, ["authorization"])

    def test_a_key_that_only_mentions_a_credential_is_not_one(self):
        """`*_env` and `*_ref` name a credential without being the value.

        This is the registry's own distinction, and the same one here: the
        config may SAY which variable the credential lives in.
        """
        self.assertEqual(shapes.key_paths_in(
            {"TOKEN_ENV": "X", "credential_ref": "vault:a/b"}), [])


class TheRefusalNamesARuleAndNeverAValue(unittest.TestCase):
    """The property that makes this safe to run at all."""

    def test_a_bypass_request_fires_on_the_line_and_not_the_code(self):
        proc = run_guard("--restricted", "-p", "context: " + "ghp_" + "A" * 36)
        self.assertEqual(proc.returncode, guard.REFUSED)
        self.assertIn(b"ARGV_CARRIES_CREDENTIAL_SHAPE", proc.stderr)
        self.assertIn(b"github-token", proc.stderr)
        self.assertIn(b"argv[2]", proc.stderr)
        # The value is on stdin and nowhere in what the guard printed.
        self.assertNotIn(b"ghp_", proc.stdout + proc.stderr)
        self.assertNotIn(b"A" * 36, proc.stdout + proc.stderr)

    def test_the_config_path_is_reported_but_its_value_never_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "mcp.json"
            secret = "ghp_" + "A" * 36
            config.write_text(json.dumps({"mcpServers": {"c": {
                "env": {"TOKEN_ENV": secret}}}}))
            proc = run_guard("--mcp-config", str(config), mcp_config=config)
            self.assertEqual(proc.returncode, guard.REFUSED)
            self.assertIn(b"TOKEN_ENV", proc.stderr)
            self.assertNotIn(secret.encode(), proc.stdout + proc.stderr)

    def test_a_config_that_will_not_parse_is_refused_rather_than_passed_through(self):
        """Fail closed: unchecked is not the same answer as clean."""
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "mcp.json"
            config.write_text("not json at all\n")
            proc = run_guard("--mcp-config", str(config), mcp_config=config)
            self.assertEqual(proc.returncode, guard.REFUSED)
            self.assertIn(b"MCP_CONFIG_UNREADABLE", proc.stderr)

    def test_a_config_that_is_not_there_is_refused_rather_than_passed_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_guard("--mcp-config", str(Path(tmp) / "absent.json"),
                             mcp_config=Path(tmp) / "absent.json")
            self.assertEqual(proc.returncode, guard.REFUSED)
            self.assertIn(b"MCP_CONFIG_UNREADABLE", proc.stderr)


class TheVocabularyIsTheOneThatAlreadyExisted(unittest.TestCase):
    """One list with two readers, and not two lists that agree today.

    `fleet/controller.py` states the rule this class enforces: "Reuses the
    transport contract's list rather than keeping a second one: two lists of
    forbidden secret names drift, and the one that drifts is the one nobody
    re-read."
    """

    def test_the_release_gate_reads_this_definition_rather_than_its_own(self):
        scanner = _load("scan_secrets_under_test", SCANNER)
        self.assertIs(scanner.RULES, shapes.SHAPES)

    def test_the_forbidden_names_come_from_the_contract_and_not_a_local_copy(self):
        """Read from the contract `fleet/transport.py` derives its own from.

        `fleet/` and `runtime/` ship in the same revision, so the file the
        guard reads is the file the fleet reads. This asserts the two agree
        without importing the fleet module at launch time.
        """
        import yaml
        declared = yaml.safe_load(
            (REPO / "fleet" / "transport-contract.yaml").read_text(encoding="utf-8"))
        expected = frozenset(
            str(n).lower() for n in declared["transport"]["forbidden_binding_fields"])
        self.assertEqual(shapes.forbidden_field_names(), expected)
        self.assertIn("authorization", expected)
        self.assertIn("token", expected)

    def test_the_guard_refuses_every_name_the_contract_forbids(self):
        """Not a sample of them — all of them, because the list is the input."""
        for name in sorted(shapes.forbidden_field_names()):
            with self.subTest(field=name):
                document = {"mcpServers": {"x": {"env": {name: "value"}}}}
                findings = guard.audit_mcp_config(json.dumps(document))
                self.assertTrue(
                    [f for f in findings
                     if f.code == guard.MCP_CONFIG_CARRIES_FORBIDDEN_KEY],
                    f"{name} is forbidden by the contract and was not refused")

    def test_a_clean_config_audits_to_nothing(self):
        """The positive control for the class above: the check can be silent."""
        document = {"mcpServers": {"continuation": {
            "command": "/rev/mcp/bin/serveros-continuation-mcp",
            "env": {"SERVEROS_CONTINUATION_ENVELOPE": "/state/env.json"}}}}
        self.assertEqual(guard.audit_mcp_config(json.dumps(document)), [])


if __name__ == "__main__":
    unittest.main()
