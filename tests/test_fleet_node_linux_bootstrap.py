"""The Linux fleet-node launchers, exercised against a fake home and a fake ssh.

What this file is for
---------------------
`fleet_mcp/node/connector.py` is platform-neutral and is already covered by its
own tests. The POSIX launchers are the new surface, and they have two ways to be
wrong that no other test would catch:

*   **They can disagree with the Python resolver about where the binding is.**
    The installer would then succeed, print a fingerprint, and leave a file the
    connector never reads -- a failure wearing the shape of a success. The
    binding path is pinned here against `local_roots.binding_root()` under the
    same environment, and the binding the installer writes is then fed back
    through `load_node_config`, so the two halves are checked against each other
    rather than against a comment.
*   **They can be unsafe about trust.** A tunnel that accepts an unknown host
    key, or a stopper that kills a pid it did not record, is not a smaller
    version of the intended thing; it is a different thing. Both are asserted as
    properties of the scripts and of a fake ssh's argv, not as prose.

Nothing here opens a socket, runs ssh, starts the connector, or touches
systemd: `ssh` and `systemctl` are fakes that record their arguments, and every
path is under a temporary HOME. The token in the fixture is a fake string, and
one test asserts the generated secret never appears in any output.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

BASH = shutil.which("bash") or "/bin/bash"

REPO = Path(__file__).resolve().parents[1]
NODE_DIR = REPO / "fleet_mcp" / "node"
RUNNABLE = ("install.sh", "start-connector.sh", "start-tunnel.sh", "stop.sh", "uninstall.sh")
ALL_SHELL = ("lib_paths.sh",) + RUNNABLE

DEVICE_REF = "device:llm-lab-dell"
ENDPOINT = "http://127.0.0.1:3001/mcp"

# A stand-in for ssh: records its argv, then fails in a chosen way. It resolves
# no host and opens no connection. Its argv is what the tests assert on, because
# the trust settings are the argv.
FAKE_SSH = """#!/usr/bin/env bash
record="${FAKE_SSH_RECORD:-/dev/null}"
{
    printf '%s\\n' "=== attempt ==="
    for arg in "$@"; do
        printf 'arg:%s\\n' "$arg"
    done
} >> "$record"
mode="${FAKE_SSH_MODE:-exit1}"
if [ "$mode" = "stuck" ]; then
    printf 'Warning: remote port forwarding failed for listen port %s\\n' "${FAKE_SSH_STUCK_PORT:-3402}" >&2
    exit 1
fi
if [ "$mode" = "sleep" ]; then
    child=""
    onterm() { [ -n "$child" ] && kill "$child" 2>/dev/null; exit 0; }
    trap onterm TERM INT
    sleep "${FAKE_SSH_SLEEP:-30}" &
    child=$!
    wait "$child"
    exit 0
fi
exit 1
"""

# A stand-in for systemctl that records its argv and reports "not active, not
# enabled", so nothing is stopped or disabled for real. It exists so a test can
# assert what the scripts ask the service manager to do -- and, in particular,
# that they never ask it to list units.
FAKE_SYSTEMCTL = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${FAKE_SYSTEMCTL_RECORD:-/dev/null}"
exit 1
"""


def parse_attempts(record: Path) -> list[list[str]]:
    attempts: list[list[str]] = []
    current: list[str] | None = None
    for line in record.read_text(encoding="utf-8").splitlines():
        if line == "=== attempt ===":
            current = []
            attempts.append(current)
        elif line.startswith("arg:") and current is not None:
            current.append(line[4:])
    return attempts


def stop_process(process: subprocess.Popen) -> None:
    """Terminate, then kill. The tunnel supervisor retries forever by design, so
    a cleanup that merely waited would hang the suite instead of failing it."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=20)


class LinuxBootstrapCase(unittest.TestCase):
    """A fake home, a fake bin directory, and an environment pinned to them."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="fleet-node-linux-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        (self.home / ".cloudcli").mkdir(parents=True)
        # A fake credential: this file is never read by anything under test, it
        # only has to exist for the preflight to report it as present.
        (self.home / ".cloudcli" / "mcp-pat-claude-code.txt").write_text(
            "fake-pat-not-a-real-token\n", encoding="utf-8"
        )
        self.bin = self.root / "bin"
        self.bin.mkdir()

    def base_env(self, **over: str) -> dict[str, str]:
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["XDG_CONFIG_HOME"] = str(self.home / ".config")
        env["XDG_STATE_HOME"] = str(self.home / ".local" / "state")
        env["SERVEROS_FLEET_PYTHON"] = sys.executable
        env["PATH"] = f"{self.bin}:{env.get('PATH', '')}"
        env["FAKE_SYSTEMCTL_RECORD"] = str(self.root / "systemctl-argv.log")
        for key in ("FLEET_MCP_HOME", "FLEET_NODE_SSH_DIR"):
            env.pop(key, None)
        env.update(over)
        return env

    # -- paths the scripts should be deriving -------------------------------

    @property
    def binding(self) -> Path:
        return self.home / ".config" / "serveros-fleet-mcp" / "fleet-node.local.yaml"

    @property
    def state(self) -> Path:
        return self.home / ".local" / "state" / "serveros-fleet-node"

    @property
    def secret(self) -> Path:
        return self.state / "secret"

    @property
    def ssh_dir(self) -> Path:
        return self.home / ".config" / "serveros-fleet-node" / "ssh"

    @property
    def tunnel_log(self) -> Path:
        return self.state / "logs" / "fleet-node-tunnel.log"

    @property
    def unit_dir(self) -> Path:
        return self.home / ".config" / "systemd" / "user"

    # -- running things -----------------------------------------------------

    def run_script(self, name: str, *args: str, env: dict[str, str] | None = None,
                   timeout: int = 60) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(NODE_DIR / name), *args],
            capture_output=True, text=True, env=env or self.base_env(),
            cwd=str(REPO), timeout=timeout,
        )

    def install(self, *extra: str, expect: int = 0, env: dict[str, str] | None = None):
        result = self.run_script(
            "install.sh",
            "--device-ref", DEVICE_REF,
            "--cloudcli-endpoint", ENDPOINT,
            *extra, env=env,
        )
        self.assertEqual(result.returncode, expect,
                         f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        return result

    def provision_ssh(self, mode: int = 0o600) -> tuple[Path, Path]:
        self.ssh_dir.mkdir(parents=True, exist_ok=True)
        identity = self.ssh_dir / "id_ed25519"
        identity.write_text("fake-private-key-not-a-real-key\n", encoding="utf-8")
        os.chmod(identity, mode)
        known_hosts = self.ssh_dir / "known_hosts"
        known_hosts.write_text("fleet-host ssh-ed25519 AAAAfakehostkey\n", encoding="utf-8")
        return identity, known_hosts

    def write_fake_ssh(self) -> Path:
        record = self.root / "ssh-argv.log"
        script = self.bin / "ssh"
        script.write_text(FAKE_SSH, encoding="utf-8")
        os.chmod(script, 0o755)
        return record

    def write_fake_systemctl(self) -> Path:
        record = self.root / "systemctl-argv.log"
        script = self.bin / "systemctl"
        script.write_text(FAKE_SYSTEMCTL, encoding="utf-8")
        os.chmod(script, 0o755)
        return record

    def link_tool(self, name: str) -> None:
        """Put one real external command on the stripped PATH. The scripts call
        `dirname` in their own preamble, so a PATH with nothing but a fake ssh in
        it fails before reaching the check under test."""
        target = shutil.which(name)
        self.assertTrue(target, f"{name} is needed by the scripts under test")
        os.symlink(target, self.bin / name)

    def python(self, code: str, *args: str, env: dict[str, str] | None = None):
        return subprocess.run(
            [sys.executable, "-c", code, str(REPO), *args],
            capture_output=True, text=True, env=env or self.base_env(),
            cwd=str(REPO), timeout=60,
        )


# ---------------------------------------------------------------------------
# The scripts themselves
# ---------------------------------------------------------------------------


class ScriptShape(LinuxBootstrapCase):
    def test_every_shell_file_parses(self) -> None:
        for name in ALL_SHELL:
            with self.subTest(script=name):
                result = subprocess.run(
                    [BASH, "-n", str(NODE_DIR / name)],
                    capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_runnable_scripts_are_executable(self) -> None:
        for name in RUNNABLE:
            with self.subTest(script=name):
                path = NODE_DIR / name
                self.assertTrue(path.is_file(), f"{name} is missing")
                self.assertTrue(os.access(path, os.X_OK), f"{name} is not executable")

    def test_help_exits_zero(self) -> None:
        for name in RUNNABLE:
            with self.subTest(script=name):
                result = self.run_script(name, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout)

    def test_no_script_invokes_a_forbidden_verb(self) -> None:
        """`list-units` renders unit Descriptions, and a Description can hold a
        Bearer token -- which is how a credential ends up in a transcript. A
        crontab entry is a persistent service nobody approved. Both are allowed
        to be *explained* in a comment or a usage line and forbidden as a
        command, so the patterns below match the command and not the prose."""
        for name in ALL_SHELL:
            text = (NODE_DIR / name).read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), start=1):
                if line.lstrip().startswith("#"):
                    continue
                with self.subTest(script=name, line=number):
                    self.assertNotIn("list-units", line)
                    self.assertFalse(re.search(r"\bcrontab\s+[-a-zA-Z/]", line),
                                     f"crontab invocation in {name}:{number}")
                    # The ssh trust options, in the form ssh would receive them.
                    # A usage line may name them while explaining the refusal.
                    self.assertFalse(re.search(r"-o\b[^\n]*StrictHostKeyChecking=no", line),
                                     f"ssh would accept any host key: {name}:{number}")
                    self.assertFalse(re.search(r"-o\b[^\n]*accept-new", line),
                                     f"ssh would accept any host key: {name}:{number}")
                    self.assertNotIn("StrictHostKeyChecking=accept-new", line)
                    self.assertNotIn("curl ", line)
                    self.assertNotIn("wget ", line)

    def test_no_script_takes_a_secret_on_the_command_line(self) -> None:
        """The leak that produced this rule: a token embedded in a systemd scope
        unit's Description, rendered by `list-units` into a session record. A
        script cannot leak a value into an argument, a unit file or a log line
        if it never receives the value at all -- so the scripts take a *path* to
        the secret and nothing else."""
        for name in ALL_SHELL:
            text = (NODE_DIR / name).read_text(encoding="utf-8")
            with self.subTest(script=name):
                self.assertNotIn("--token", text)
                self.assertNotIn("FLEET_NODE_SECRET", text,
                                 "the secret is read from its file, never from the environment")
        # The rendering surface is three templates and one set of placeholders.
        # None of them is a credential, and this is the list, not a promise.
        placeholders: set[str] = set()
        for template in sorted((NODE_DIR / "systemd" / "user").glob("*.in")):
            placeholders |= set(re.findall(r"@[A-Z_]+@", template.read_text(encoding="utf-8")))
        self.assertEqual(placeholders, {
            "@REPO_ROOT@", "@BINDING@", "@SSH_HOST@", "@REMOTE_PORT@",
            "@LOCAL_PORT@", "@IDENTITY@", "@KNOWN_HOSTS@",
        })
        # The secret is generated into a file and read only to hash it.
        self.assertIn("sha256sum", (NODE_DIR / "install.sh").read_text(encoding="utf-8"))

    def test_units_do_not_reference_the_test_only_switch(self) -> None:
        tunnel_unit = (NODE_DIR / "systemd" / "user" /
                       "serveros-fleet-node-tunnel.service.in").read_text(encoding="utf-8")
        self.assertNotIn("--max-attempts", tunnel_unit)


# ---------------------------------------------------------------------------
# The path derivation: the shell and the Python resolver must agree
# ---------------------------------------------------------------------------


class ShellPathsAgreeWithPython(LinuxBootstrapCase):
    PY_QUERY = (
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from fleet_mcp.local_roots import binding_candidates, binding_root\n"
        "print(binding_root())\n"
        "print(binding_candidates('fleet-node.local.yaml')[0])\n"
    )
    SH_QUERY = (
        ". '%s'\n"
        "fleet_binding_root\n"
        "fleet_binding_file\n"
    )

    def resolve_both(self, env: dict[str, str]) -> tuple[list[str], list[str]]:
        py = self.python(self.PY_QUERY, env=env)
        self.assertEqual(py.returncode, 0, py.stderr)
        sh = subprocess.run(
            [BASH, "-c", self.SH_QUERY % (NODE_DIR / "lib_paths.sh")],
            capture_output=True, text=True, env=env, timeout=60,
        )
        self.assertEqual(sh.returncode, 0, sh.stderr)
        return py.stdout.split(), sh.stdout.split()

    def test_binding_root_matches_under_every_environment(self) -> None:
        """A disagreement here is the silent failure this whole file exists for:
        the installer writes a binding where the connector does not look."""
        cases = [
            ("the defaults", {}),
            ("an explicit XDG_CONFIG_HOME", {"XDG_CONFIG_HOME": str(self.root / "cfg")}),
            ("FLEET_MCP_HOME relocating both roots", {"FLEET_MCP_HOME": str(self.root / "fleet")}),
            ("both, with FLEET_MCP_HOME winning", {
                "FLEET_MCP_HOME": str(self.root / "fleet"),
                "XDG_CONFIG_HOME": str(self.root / "cfg"),
            }),
        ]
        for label, over in cases:
            with self.subTest(label):
                python_side, shell_side = self.resolve_both(self.base_env(**over))
                self.assertEqual(python_side, shell_side)

    def test_the_node_state_root_is_not_the_binding_root(self) -> None:
        """A binding and mutable runtime state do not share a root: the binding
        is meant to stay re-derivable, and a file that accumulates state stops
        being either."""
        result = subprocess.run(
            [BASH, "-c",
             f". '{NODE_DIR / 'lib_paths.sh'}'; "
             "fleet_state_root; fleet_secret_file; fleet_log_dir; fleet_tunnel_pid_file"],
            capture_output=True, text=True, env=self.base_env(), timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        state_root, secret, log_dir, pid = result.stdout.split()
        self.assertEqual(state_root, str(self.state))
        self.assertEqual(secret, str(self.secret))
        self.assertEqual(log_dir, str(self.state / "logs"))
        self.assertEqual(pid, str(self.state / "tunnel.pid"))
        self.assertFalse(state_root.startswith(str(self.home / ".config")))
        self.assertTrue(secret.startswith(state_root))


# ---------------------------------------------------------------------------
# install.sh
# ---------------------------------------------------------------------------


class Installer(LinuxBootstrapCase):
    def test_writes_a_binding_and_a_secret_and_prints_only_a_fingerprint(self) -> None:
        result = self.install()

        self.assertTrue(self.binding.is_file())
        self.assertEqual(self.binding.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.secret.is_file())
        self.assertEqual(self.secret.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)

        raw = self.secret.read_bytes()
        self.assertEqual(len(raw), 64, "the secret must be 64 bytes with no trailing newline")
        self.assertRegex(raw.decode(), r"^[0-9a-f]{64}$")

        # The whole point: the value never reaches stdout or stderr, but a
        # fingerprint of it does.
        combined = result.stdout + result.stderr
        self.assertNotIn(raw.decode(), combined)
        fingerprint = hashlib.sha256(raw).hexdigest()[:12]
        self.assertIn(fingerprint, result.stdout)

        # The fake credential is named as an anchor, never copied.
        self.assertIn(str(self.home / ".cloudcli" / "mcp-pat-claude-code.txt"), result.stdout)
        self.assertNotIn("fake-pat-not-a-real-token", combined)

    def test_binding_has_the_schema_the_connector_declares(self) -> None:
        import yaml

        self.install()
        raw = yaml.safe_load(self.binding.read_text(encoding="utf-8"))
        self.assertEqual(raw["schema"], "serveros/fleet-node.local/v1")
        self.assertEqual(raw["device_ref"], DEVICE_REF)
        self.assertEqual(raw["secret_anchor"], str(self.secret))
        self.assertEqual(raw["cloudcli_endpoint"], ENDPOINT)
        self.assertEqual(raw["bind"], "127.0.0.1")
        self.assertEqual(raw["port"], 3402)
        self.assertEqual(raw["allow_send_to"], [])
        self.assertIn("notes", raw)

    def test_the_connector_itself_can_load_what_the_installer_wrote(self) -> None:
        """The two halves checked against each other rather than against a
        comment: the installer's output goes through the connector's own
        reader."""
        self.install()
        code = (
            "import json, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from fleet_mcp.node.connector import load_node_config\n"
            "cfg = load_node_config(sys.argv[2])\n"
            "print(json.dumps({'device_ref': cfg.device_ref, 'bind': cfg.bind, 'port': cfg.port,\n"
            "                  'allow_send_to': list(cfg.allow_send_to),\n"
            "                  'secret_len': len(cfg.secret() or ''),\n"
            "                  'endpoint': cfg.cloudcli_endpoint}))\n"
        )
        result = self.python(code, str(self.binding))
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["device_ref"], DEVICE_REF)
        self.assertEqual(parsed["bind"], "127.0.0.1")
        self.assertEqual(parsed["port"], 3402)
        self.assertEqual(parsed["allow_send_to"], [], "the send allow list must ship empty")
        self.assertEqual(parsed["secret_len"], 64)
        self.assertEqual(parsed["endpoint"], ENDPOINT)

    def test_rerunning_changes_nothing(self) -> None:
        self.install()
        first_binding = self.binding.read_bytes()
        first_secret = self.secret.read_bytes()
        second = self.install()
        self.assertEqual(self.binding.read_bytes(), first_binding)
        self.assertEqual(self.secret.read_bytes(), first_secret)
        self.assertIn("kept existing", second.stdout)

    def test_it_creates_no_ssh_material(self) -> None:
        """Provisioning a key is a decision with a consequence at the far end.
        A key minted here would be a trust relationship the Fleet host never
        agreed to, and the failure would surface there as a refused key."""
        self.install()
        self.assertFalse((self.ssh_dir / "id_ed25519").exists())
        self.assertFalse((self.ssh_dir / "known_hosts").exists())

    def test_it_does_not_call_systemctl(self) -> None:
        """Enabling a service is a change to the account, so the installer writes
        the unit files and stops. A fake systemctl on PATH records nothing."""
        record = self.write_fake_systemctl()
        self.provision_ssh()
        self.install("--systemd", "--ssh-host", "fleet.example", "--remote-port", "3410")
        self.assertFalse(record.exists(),
                         "install.sh must not enable, start or reload anything")
        self.assertTrue((self.unit_dir / "serveros-fleet-node-tunnel.service").exists())

    def test_identity_and_endpoint_have_no_default(self) -> None:
        for missing in ("--device-ref", "--cloudcli-endpoint"):
            with self.subTest(missing):
                args = ["--device-ref", DEVICE_REF, "--cloudcli-endpoint", ENDPOINT]
                index = args.index(missing)
                del args[index:index + 2]
                result = self.run_script("install.sh", *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(missing, result.stderr)

    def test_refuses_a_device_ref_that_is_not_a_typed_ref(self) -> None:
        for bad in ("llm-lab-dell", "device:LLM-LAB-DELL", "device:", "host:llm-lab"):
            with self.subTest(bad):
                result = self.install("--device-ref", bad, expect=2)
                self.assertIn("device:", result.stderr)
                self.assertFalse(self.binding.exists())

    def test_refuses_an_endpoint_that_is_not_on_loopback(self) -> None:
        """An endpoint on a routable address would put a CloudCLI credential one
        hop from the network, which is the arrangement the whole tunnel design
        exists to avoid."""
        # 192.0.2.0/24 is TEST-NET-1: reserved for documentation, so this address
        # cannot be mistaken for a real one on somebody's network.
        result = self.install("--cloudcli-endpoint", "http://192.0.2.10:3001/mcp", expect=2)
        self.assertIn("loopback", result.stdout + result.stderr)
        self.assertFalse(self.binding.exists())

    def test_refuses_to_write_a_binding_inside_a_repository(self) -> None:
        repo_like = self.root / "checkout"
        (repo_like / ".git").mkdir(parents=True)
        env = self.base_env(XDG_CONFIG_HOME=str(repo_like / "cfg"))
        result = self.run_script(
            "install.sh", "--device-ref", DEVICE_REF, "--cloudcli-endpoint", ENDPOINT,
            env=env,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("repository", result.stderr)
        self.assertFalse((repo_like / "cfg" / "serveros-fleet-mcp").exists())

    def test_refuses_the_closed_root(self) -> None:
        """`~/.serveros` is closed to five named files by
        fleet/node-root-contract.yaml; a binding is not one of them, and a
        printed remedy the product then refuses is worse than a missing file."""
        closed = self.home / ".serveros"
        closed.mkdir()
        env = self.base_env(XDG_CONFIG_HOME=str(closed))
        result = self.run_script(
            "install.sh", "--device-ref", DEVICE_REF, "--cloudcli-endpoint", ENDPOINT,
            env=env,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("closed", result.stderr)
        self.assertFalse((closed / "serveros-fleet-mcp").exists())

    def test_preflight_writes_nothing_and_reports_what_is_missing(self) -> None:
        result = self.install("--preflight", "--systemd", "--ssh-host", "fleet.example",
                              "--remote-port", "3410", expect=1)
        self.assertFalse(self.binding.exists())
        self.assertFalse(self.secret.exists())
        self.assertFalse(self.ssh_dir.exists())
        self.assertIn("no SSH identity", result.stdout)

    def test_preflight_passes_when_everything_is_provisioned(self) -> None:
        self.provision_ssh()
        result = self.install("--preflight", "--systemd", "--ssh-host", "fleet.example",
                              "--remote-port", "3410")
        self.assertIn("All checks passed", result.stdout)
        self.assertFalse(self.binding.exists(), "--preflight must write nothing")


class SystemdUnits(LinuxBootstrapCase):
    def install_with_units(self, *extra: str):
        self.provision_ssh()
        return self.install("--systemd", "--ssh-host", "fleet.example",
                            "--remote-port", "3410", *extra)

    def test_renders_both_units_with_every_token_substituted(self) -> None:
        result = self.install_with_units()
        for name in ("serveros-fleet-node-connector.service",
                     "serveros-fleet-node-tunnel.service"):
            path = self.unit_dir / name
            with self.subTest(unit=name):
                self.assertTrue(path.is_file(), result.stdout)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("@", re.sub(r"@[A-Za-z_]+@", "", text),
                                 "an unsubstituted template token survived")
                self.assertIn("ExecStart=", text)
                self.assertIn("Restart=always", text)

    def test_units_carry_no_secret_and_no_place_to_put_one(self) -> None:
        self.install_with_units()
        secret = self.secret.read_text(encoding="utf-8")
        for path in self.unit_dir.iterdir():
            text = path.read_text(encoding="utf-8")
            with self.subTest(unit=path.name):
                self.assertNotIn(secret, text)
                self.assertNotIn("Environment=", text)
                self.assertNotIn("EnvironmentFile=", text)
                self.assertNotIn("PasswordAuthentication", text)
                for line in text.splitlines():
                    if line.startswith("Description="):
                        self.assertNotIn(secret, line)
                        self.assertNotEqual(line, "Description=")

    def test_units_name_absolute_paths_and_the_assigned_port(self) -> None:
        self.install_with_units()
        tunnel = (self.unit_dir / "serveros-fleet-node-tunnel.service").read_text(encoding="utf-8")
        identity, known_hosts = self.ssh_dir / "id_ed25519", self.ssh_dir / "known_hosts"
        self.assertIn(f"--identity {identity}", tunnel)
        self.assertIn(f"--known-hosts {known_hosts}", tunnel)
        self.assertIn("--remote-port 3410", tunnel)
        self.assertIn("--local-port 3402", tunnel)
        self.assertNotIn("StrictHostKeyChecking=no", tunnel)
        connector = (self.unit_dir / "serveros-fleet-node-connector.service").read_text(encoding="utf-8")
        self.assertIn(f"--config {self.binding}", connector)
        self.assertIn(str(NODE_DIR), connector)

    def test_systemd_refuses_without_the_things_the_units_need(self) -> None:
        """`--systemd` without a host or an assigned port: a unit that starts and
        fails forever is worse than a refusal, so the check is fatal here even
        though it is only a warning without --systemd."""
        for label, extra in (
            ("neither host nor port", ["--systemd"]),
            ("no remote port", ["--systemd", "--ssh-host", "fleet.example"]),
        ):
            with self.subTest(label):
                self.provision_ssh()
                result = self.install(*extra, expect=2)
                self.assertIn("--systemd needs", result.stdout)
                self.assertFalse((self.unit_dir / "serveros-fleet-node-tunnel.service").exists())
                self.assertFalse(self.binding.exists(), "a failed install writes no binding")

    def test_systemd_refuses_without_a_pinned_host_key(self) -> None:
        identity, _ = self.provision_ssh()
        (self.ssh_dir / "known_hosts").unlink()
        result = self.install("--systemd", "--ssh-host", "fleet.example",
                              "--remote-port", "3410", expect=2)
        self.assertIn("known_hosts", result.stdout)


# ---------------------------------------------------------------------------
# start-connector.sh
# ---------------------------------------------------------------------------


class ConnectorLauncher(LinuxBootstrapCase):
    def test_refuses_without_a_binding_and_says_what_to_run(self) -> None:
        result = self.run_script("start-connector.sh")
        self.assertEqual(result.returncode, 3)
        self.assertIn("install.sh", result.stderr)

    def test_refuses_without_an_interpreter(self) -> None:
        """A launcher that cannot find its interpreter has to say so: it is
        started by systemd, and a unit that starts and does nothing is the
        failure mode this exit code exists to prevent."""
        self.install()
        self.link_tool("dirname")
        env = self.base_env(PATH=str(self.bin))
        env.pop("SERVEROS_FLEET_PYTHON", None)
        result = subprocess.run(
            [BASH, str(NODE_DIR / "start-connector.sh")],
            capture_output=True, text=True, env=env, cwd=str(REPO), timeout=60,
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("python3", result.stderr)

    def test_it_names_the_binding_it_reads(self) -> None:
        text = (NODE_DIR / "start-connector.sh").read_text(encoding="utf-8")
        self.assertIn("--config", text)
        self.assertIn("PYTHONPATH", text)
        self.assertIn("cd --", text, "the working directory is pinned to the tree it imports")


# ---------------------------------------------------------------------------
# start-tunnel.sh
# ---------------------------------------------------------------------------


class TunnelPreflight(LinuxBootstrapCase):
    BASE = ("--ssh-host", "fleet.example", "--remote-port", "3410")

    def test_no_identity_is_a_refusal_before_ssh_is_invoked(self) -> None:
        record = self.write_fake_ssh()
        result = self.run_script("start-tunnel.sh", *self.BASE)
        self.assertEqual(result.returncode, 3)
        self.assertIn("No SSH identity", result.stderr)
        self.assertFalse(record.exists(), "ssh must not be invoked at all")
        self.assertFalse(self.ssh_dir.exists(), "the identity must not be created")

    def test_no_pinned_host_key_is_a_refusal_before_ssh_is_invoked(self) -> None:
        self.provision_ssh()
        (self.ssh_dir / "known_hosts").unlink()
        record = self.write_fake_ssh()
        result = self.run_script("start-tunnel.sh", *self.BASE)
        self.assertEqual(result.returncode, 3)
        self.assertIn("will not trust a host on first sight", result.stderr)
        self.assertFalse(record.exists())

    def test_an_empty_known_hosts_is_not_a_pinned_host_key(self) -> None:
        self.provision_ssh()
        (self.ssh_dir / "known_hosts").write_text("")
        record = self.write_fake_ssh()
        result = self.run_script("start-tunnel.sh", *self.BASE)
        self.assertEqual(result.returncode, 3)
        self.assertFalse(record.exists())

    def test_a_loose_identity_mode_is_refused(self) -> None:
        self.provision_ssh(mode=0o644)
        record = self.write_fake_ssh()
        result = self.run_script("start-tunnel.sh", *self.BASE)
        self.assertEqual(result.returncode, 3)
        self.assertIn("group- or world-readable", result.stderr)
        self.assertFalse(record.exists())

    def test_the_host_and_the_remote_port_have_no_default(self) -> None:
        self.provision_ssh()
        for missing in ("--ssh-host", "--remote-port"):
            with self.subTest(missing):
                args = list(self.BASE)
                index = args.index(missing)
                del args[index:index + 2]
                result = self.run_script("start-tunnel.sh", *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(missing, result.stderr)

    def test_a_host_that_is_an_argument_list_is_refused(self) -> None:
        self.provision_ssh()
        result = self.run_script("start-tunnel.sh", "--ssh-host", "-oProxyCommand=boom",
                                 "--remote-port", "3410")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dash", result.stderr)


class TunnelArgv(LinuxBootstrapCase):
    """The trust settings are the argv, so the argv is what gets asserted."""

    def run_one_attempt(self, mode: str = "exit1", **env_over: str):
        identity, known_hosts = self.provision_ssh()
        record = self.write_fake_ssh()
        env = self.base_env(FAKE_SSH_RECORD=str(record), FAKE_SSH_MODE=mode, **env_over)
        result = self.run_script(
            "start-tunnel.sh",
            "--ssh-host", "fleet.example", "--remote-port", "3410",
            "--identity", str(identity), "--known-hosts", str(known_hosts),
            "--retry-seconds", "0", "--slow-retry-seconds", "0", "--max-attempts", "1",
            env=env,
        )
        return result, record, identity, known_hosts

    def test_one_attempt_pins_trust_and_forwards_the_assigned_port(self) -> None:
        result, record, identity, known_hosts = self.run_one_attempt()
        self.assertEqual(result.returncode, 0, result.stderr)
        attempts = parse_attempts(record)
        self.assertEqual(len(attempts), 1)
        argv = attempts[0]

        self.assertIn("-N", argv)
        self.assertEqual(argv[argv.index("-i") + 1], str(identity))
        self.assertIn("IdentitiesOnly=yes", argv)
        self.assertIn(f"UserKnownHostsFile={known_hosts}", argv)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("ExitOnForwardFailure=yes", argv)
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("ServerAliveInterval=30", argv)
        self.assertEqual(argv[argv.index("-R") + 1], "127.0.0.1:3410:127.0.0.1:3402")
        self.assertEqual(argv[-1], "fleet.example", "the host is the last argument")
        self.assertLess(argv.index("-i"), argv.index("-R"),
                        "the key is chosen before the forward is requested")

        joined = " ".join(argv)
        self.assertNotIn("StrictHostKeyChecking=no", joined)
        self.assertNotIn("accept-new", joined)
        self.assertNotIn("ProxyCommand", joined)

    def test_the_local_port_can_be_moved_without_moving_the_remote_one(self) -> None:
        identity, known_hosts = self.provision_ssh()
        record = self.write_fake_ssh()
        env = self.base_env(FAKE_SSH_RECORD=str(record), FAKE_SSH_MODE="exit1")
        self.run_script(
            "start-tunnel.sh", "--ssh-host", "fleet.example", "--remote-port", "3410",
            "--local-port", "3402", "--identity", str(identity),
            "--known-hosts", str(known_hosts),
            "--retry-seconds", "0", "--slow-retry-seconds", "0", "--max-attempts", "1",
            env=env,
        )
        argv = parse_attempts(record)[0]
        self.assertEqual(argv[argv.index("-R") + 1], "127.0.0.1:3410:127.0.0.1:3402")


class TunnelSupervisor(LinuxBootstrapCase):
    def run_supervisor(self, mode: str = "exit1", attempts: str = "1", *extra: str,
                       retry: str = "0", slow: str | None = "0", **env_over: str):
        """`slow=None` leaves --slow-retry-seconds off the command line, so the
        script's own default applies -- which is the value a deployment gets."""
        identity, known_hosts = self.provision_ssh()
        record = self.write_fake_ssh()
        env = self.base_env(FAKE_SSH_RECORD=str(record), FAKE_SSH_MODE=mode,
                            FAKE_SSH_STUCK_PORT="3410", **env_over)
        slow_args = [] if slow is None else ["--slow-retry-seconds", slow]
        result = self.run_script(
            "start-tunnel.sh",
            "--ssh-host", "fleet.example", "--remote-port", "3410",
            "--identity", str(identity), "--known-hosts", str(known_hosts),
            "--retry-seconds", retry, *slow_args,
            "--max-attempts", attempts, *extra,
            env=env, timeout=120,
        )
        return result, self.tunnel_log.read_text(encoding="utf-8")

    def test_a_short_lived_exit_backs_off_to_the_slow_retry(self) -> None:
        result, log = self.run_supervisor(attempts="1", retry="1", slow=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Both values are left at their defaults: slow retry 60, retry 1, so
        # 4x(1)=4 is smaller and the slow retry wins -- the same rule, and the
        # same numbers, as the Windows node.
        self.assertIn("retrying in 60s", log)
        self.assertIn("not retrying: --max-attempts 1 reached", log)

    def test_a_tunnel_that_stayed_up_uses_the_normal_retry(self) -> None:
        """Five seconds is the boundary: below it the exit is read as a
        configuration problem, at or above it as an interruption."""
        result, log = self.run_supervisor(mode="sleep", attempts="1", retry="1", slow="0",
                                          FAKE_SSH_SLEEP="7")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("retrying in 1s", log)
        self.assertNotIn("retrying in 60s", log)

    def test_a_stuck_remote_port_is_reported_once_and_named(self) -> None:
        _result, log = self.run_supervisor(mode="stuck", attempts="3")
        self.assertEqual(log.count("INCIDENT remote-port-stuck"), 1)
        self.assertIn("consecutive failed attempts: 1", log)
        self.assertIn("127.0.0.1:3410", log)
        self.assertIn("Waiting does not fix this", log)
        self.assertIn("pkill -f", log, "the log names the command that clears it")

    def test_a_stuck_remote_port_is_repeated_every_thirtieth_attempt(self) -> None:
        _result, log = self.run_supervisor(mode="stuck", attempts="40")
        self.assertEqual(log.count("INCIDENT remote-port-stuck"), 2,
                         "once, and then every thirtieth attempt -- not every attempt")

    def test_an_ordinary_failure_does_not_report_an_incident(self) -> None:
        _result, log = self.run_supervisor(mode="exit1", attempts="3")
        self.assertNotIn("INCIDENT", log)
        self.assertIn("tunnel exited after", log)

    def test_the_pid_file_exists_only_while_the_tunnel_does(self) -> None:
        identity, known_hosts = self.provision_ssh()
        self.write_fake_ssh()
        env = self.base_env(FAKE_SSH_RECORD=str(self.root / "ssh-argv.log"),
                            FAKE_SSH_MODE="sleep", FAKE_SSH_SLEEP="30")
        supervisor = subprocess.Popen(
            [str(NODE_DIR / "start-tunnel.sh"),
             "--ssh-host", "fleet.example", "--remote-port", "3410",
             "--identity", str(identity), "--known-hosts", str(known_hosts),
             "--retry-seconds", "0", "--slow-retry-seconds", "0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd=str(REPO),
        )
        self.addCleanup(stop_process, supervisor)
        pid_file = self.state / "tunnel.pid"
        forward_file = self.state / "tunnel.forward"
        for _ in range(100):
            if pid_file.is_file() and forward_file.is_file():
                break
            time.sleep(0.05)
        self.assertTrue(pid_file.is_file(), "the supervisor must record its child's pid")
        recorded = int(pid_file.read_text().strip())
        self.assertNotEqual(recorded, supervisor.pid,
                            "the pid on record is the tunnel's, not the supervisor's")
        self.assertEqual(forward_file.read_text().strip(), "127.0.0.1:3410:127.0.0.1:3402")

        cmdline = Path(f"/proc/{recorded}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        self.assertIn("127.0.0.1:3410:127.0.0.1:3402", cmdline,
                      "the recorded pid is the ssh that carries this forward")

        supervisor.terminate()
        supervisor.wait(timeout=20)
        self.assertFalse(pid_file.exists(), "the pid file must not outlive the tunnel")
        self.assertFalse(forward_file.exists())
        for _ in range(100):
            if not Path(f"/proc/{recorded}").exists():
                break
            time.sleep(0.05)
        self.assertFalse(Path(f"/proc/{recorded}").exists(),
                         "stopping the supervisor must stop its ssh")


# ---------------------------------------------------------------------------
# stop.sh and uninstall.sh
# ---------------------------------------------------------------------------


class Stopper(LinuxBootstrapCase):
    def setUp(self) -> None:
        super().setUp()
        # A fake systemctl, so that "stop the unit" is never a real change to this
        # account, and so that what the scripts ask the manager is on record.
        self.systemctl_record = self.write_fake_systemctl()

    def spawn_with_argv(self, *argv: str) -> subprocess.Popen:
        process = subprocess.Popen(list(argv), stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        self.addCleanup(_terminate, process)
        return process

    def spawn_decoy_tunnel(self, forward: str) -> subprocess.Popen:
        """A process whose argv carries the recorded forward spec, which is what
        makes it look like a tunnel to the guard. Python rather than `sleep`,
        because a python process dies on the SIGTERM that stop.sh sends."""
        return self.spawn_with_argv(
            sys.executable, "-c", "import time; time.sleep(60)",
            "-N", "-R", forward, "fleet.example",
        )

    def test_a_pid_that_was_recycled_is_not_killed(self) -> None:
        """The pid file records a number, and a number that was ours once can be
        somebody else's now -- a decoy with a different command line must
        survive."""
        self.install()
        decoy = self.spawn_with_argv("sleep", "60")
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "tunnel.pid").write_text(f"{decoy.pid}\n")
        (self.state / "tunnel.forward").write_text("127.0.0.1:3410:127.0.0.1:3402\n")

        result = self.run_script("stop.sh")
        self.assertIn("recycled pid", result.stderr)
        self.assertIsNone(decoy.poll(), "the decoy must still be running")
        self.assertFalse((self.state / "tunnel.pid").exists())

    def test_the_recorded_tunnel_is_killed(self) -> None:
        self.install()
        forward = "127.0.0.1:3410:127.0.0.1:3402"
        tunnel = self.spawn_decoy_tunnel(forward)
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "tunnel.pid").write_text(f"{tunnel.pid}\n")
        (self.state / "tunnel.forward").write_text(forward + "\n")

        result = self.run_script("stop.sh")
        self.assertIn(f"stopping tunnel pid {tunnel.pid}", result.stdout)
        tunnel.wait(timeout=20)
        self.assertIsNotNone(tunnel.poll())
        self.assertFalse((self.state / "tunnel.pid").exists())
        self.assertFalse((self.state / "tunnel.forward").exists())

    def test_the_connector_is_matched_on_its_module_and_its_binding(self) -> None:
        """Either alone is too wide: the module name matches a second checkout,
        and the binding path alone matches a `grep`."""
        self.install()
        real = self.spawn_with_argv(
            sys.executable, "-c", "import time; time.sleep(60)",
            "-m", "fleet_mcp.node.connector", "--config", str(self.binding),
        )
        other_checkout = self.spawn_with_argv(
            sys.executable, "-c", "import time; time.sleep(60)",
            "-m", "fleet_mcp.node.connector", "--config", str(self.root / "other.yaml"),
        )
        mentions_the_path = self.spawn_with_argv(
            sys.executable, "-c", "import time; time.sleep(60)", "--config", str(self.binding),
        )

        result = self.run_script("stop.sh")
        self.assertIn(f"stopping connector pid {real.pid}", result.stdout)
        real.wait(timeout=20)
        self.assertIsNotNone(real.poll())
        self.assertIsNone(other_checkout.poll(), "another checkout's connector is not ours")
        self.assertIsNone(mentions_the_path.poll(), "a process that merely names the path is not ours")

    def test_dry_run_stops_nothing(self) -> None:
        self.install()
        forward = "127.0.0.1:3410:127.0.0.1:3402"
        tunnel = self.spawn_decoy_tunnel(forward)
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "tunnel.pid").write_text(f"{tunnel.pid}\n")
        (self.state / "tunnel.forward").write_text(forward + "\n")

        result = self.run_script("stop.sh", "--dry-run")
        self.assertIn("dry run", result.stdout)
        self.assertIsNone(tunnel.poll())
        self.assertTrue((self.state / "tunnel.pid").exists())

    def test_it_asks_systemctl_about_its_own_units_and_never_lists_units(self) -> None:
        self.install()
        self.run_script("stop.sh")
        asked = self.systemctl_record.read_text(encoding="utf-8").splitlines()
        self.assertTrue(asked, "the stopper should ask the manager about its units")
        for line in asked:
            tokens = line.split()
            self.assertNotIn("list-units", tokens)
            self.assertIn("serveros-fleet-node-", line,
                          "only this installation's two units are ever named")
            self.assertTrue(
                any(t in {"is-active", "stop", "is-enabled", "disable", "daemon-reload"}
                    for t in tokens),
                f"unexpected ask: {line}",
            )


class Uninstaller(LinuxBootstrapCase):
    def install_with_units(self):
        self.provision_ssh()
        self.install("--systemd", "--ssh-host", "fleet.example", "--remote-port", "3410")

    def test_it_removes_the_units_and_keeps_the_values(self) -> None:
        self.write_fake_systemctl()
        self.install_with_units()
        binding_before = self.binding.read_bytes()
        secret_before = self.secret.read_bytes()

        result = self.run_script("uninstall.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.unit_dir / "serveros-fleet-node-connector.service").exists())
        self.assertFalse((self.unit_dir / "serveros-fleet-node-tunnel.service").exists())
        self.assertEqual(self.binding.read_bytes(), binding_before)
        self.assertEqual(self.secret.read_bytes(), secret_before)
        self.assertIn("kept node secret", result.stdout)
        self.assertIn("kept binding", result.stdout)
        self.assertIn("no route", result.stdout)

    def test_purge_removes_the_values_only_when_asked(self) -> None:
        self.write_fake_systemctl()
        self.install_with_units()
        result = self.run_script("uninstall.sh", "--purge-secret", "--purge-binding")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.secret.exists())
        self.assertFalse(self.binding.exists())
        self.assertIn("the Fleet host still holds the matching value", result.stdout)

    def test_dry_run_removes_nothing(self) -> None:
        self.write_fake_systemctl()
        self.install_with_units()
        result = self.run_script("uninstall.sh", "--dry-run", "--purge-secret", "--purge-binding")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.secret.exists())
        self.assertTrue(self.binding.exists())
        self.assertTrue((self.unit_dir / "serveros-fleet-node-connector.service").exists())


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.kill()
        process.wait(timeout=20)


if __name__ == "__main__":
    unittest.main()
