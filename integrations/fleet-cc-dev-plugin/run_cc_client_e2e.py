#!/usr/bin/env python3
"""Drive a real Claude Code client against the fixture or the real owned stdio runtime.

The receipt is four separate claims, because one can be true while another is
false and an earlier revision of this lane reported one as another:

1. **PLUGIN_LOAD_OK** — `claude plugin validate --strict` and
   `claude --plugin-dir … plugin details` enumerate the shared Skills and one MCP
   component. This is a directory/registration fact. It enumerates no tool.
2. **MCP_INITIALIZE_OK / TOOLS_LIST_OK** — `claude --plugin-dir … mcp list`
   actually starts the stdio server, sends `initialize`,
   `notifications/initialized` and `tools/list`, and reads the tool count the
   server serves. The client's own wire is the evidence; "Connected" alone is
   not, and the separate probe is not.
3. **AUTH_SCOPE** — the principal holds exactly `fleet:read`.
   `tools/list` still advertises every served tool (discovery is not entitlement),
   and a `fleet_send_to_session` call is refused `SCOPE_DENIED` before the fake
   provider is reached.
4. **CLIENT_E2E** — only when `--model-readbacks` is passed: a real `claude -p`
   session, with only the three read tools allowed, calls
   `fleet_list_devices`, `fleet_list_sessions` and
   `fleet_cloudcli_native_capability`; the wire log records each call and a
   non-error result. The model can decline; that is recorded, not hidden.

The fixture is local stdio over `device:fixture-node`. It is **LOCAL**, not
REAL_DEVICE, and it never contacts the live Fleet ingress. No credential is
read, copied or printed. The Claude Code configuration directory is a fresh
directory under `--out`, so the user's global plugin/MCP configuration is not
modified.

Usage:

    python3 integrations/fleet-cc-dev-plugin/run_cc_client_e2e.py \
        --out /path/to/evidence [--model-readbacks]

Exit status: 0 when every requested check passed; 1 when a requested check
failed; 2 when the runner could not run (no `claude`, bad output dir).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# The runner imports the builder and the fixture; none of that may leave a
# `__pycache__` under the source tree it is measuring.
sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

import build_local_fixture as fixture  # noqa: E402
import build_cc_plugin as cc_builder  # noqa: E402
import fleet_owned_probe  # noqa: E402
from stdio_framing import write_all_fd  # noqa: E402

DEVICE = "device:fixture-node"
SESSION_REF = "device:fixture-node|claude|fixture-session-0001"
READ_TOOLS = (
    "fleet_list_devices",
    "fleet_list_sessions",
    "fleet_cloudcli_native_capability",
)
PLUGIN_NAME = fixture.FIXTURE_NAME
SERVER_NAME = fixture.SERVER_NAME
QUALIFIED_SERVER = f"plugin:{PLUGIN_NAME}:{SERVER_NAME}"
SHARED_SKILLS = ROOT / "integrations" / "fleet-v1-first-install" / "plugin" / "skills"

#: The documented `.claude-plugin/plugin.json` top-level keys. An unknown key is
#: stripped with a warning at load, so a fixture that invented one would lose it
#: silently; the acceptance check names the stray key instead.
DOCUMENTED_MANIFEST_KEYS = {
    "$schema", "name", "displayName", "version", "description", "author",
    "homepage", "repository", "license", "keywords", "metadata", "icon",
    "documentationUrl", "supportUrl", "privacyPolicyUrl", "termsOfServiceUrl",
    "defaultEnabled", "dependencies", "settings", "userConfig", "types",
    "channels", "skills", "commands", "agents", "hooks", "mcpServers",
    "lspServers", "outputStyles", "workflows", "experimental",
}
# Claude Code normalizes `plugin:<plugin>:<server>` into the tool-name server
# segment by replacing `:` with `_` and prefixing `plugin_`. Measured on
# 2.1.272 from a permission-denial receipt; asserted here by the model run.
TOOL_PREFIX = f"mcp__plugin_{PLUGIN_NAME}_{SERVER_NAME}__"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _read_json_lines(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with path.open("rb") as stream:
        data = stream.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        return [{"event": "observation_failed", "reason": "wire log byte budget"}]
    for line in data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            try:
                row = json.loads(line)
                rows.append(row if isinstance(row, dict) else {"event": "unparseable"})
            except json.JSONDecodeError:
                rows.append({"event": "unparseable"})
            if len(rows) > 8192:
                return [{"event": "observation_failed", "reason": "wire log frame budget"}]
    return rows


def _json_from_output(text: str) -> dict[str, Any] | None:
    """The CLI may print a warning line before JSON; the object is the payload."""
    for index, line in enumerate(text.splitlines()):
        if line.startswith("{"):
            try:
                return json.loads("\n".join(text.splitlines()[index:]))
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


class Runner:
    def __init__(self, out: Path, model_readbacks: bool, *,
                 owned_entry: str | None = None,
                 owned_config: str | None = None,
                 owned_state_dir: str = "/data/home/vince/state/serveros") -> None:
        self.owned_entry = owned_entry
        self.owned_config = owned_config
        self.owned_state_dir = owned_state_dir
        self.plugin_name = "serveros-fleet-dev" if owned_entry else fixture.FIXTURE_NAME
        self.server_name = "serveros-fleet" if owned_entry else fixture.SERVER_NAME
        self.qualified_server = f"plugin:{self.plugin_name}:{self.server_name}"
        self.owned_result: dict | None = None
        self.interposer_log = None
        self.out = out
        # Named apart from the method below: a bool attribute with the method's
        # name shadows it, and the call becomes "'bool' object is not callable".
        self.want_model_readbacks = model_readbacks
        self.plugin = out / "plugin"
        self.cc_home = out / "cc-home"
        self.session_cwd = out / "session-cwd"
        self.logs = out / "logs"
        self.tmp = out / "tmp"
        self.results: dict[str, Any] = {}
        self.claude = shutil.which("claude")
        self.claude_version: str | None = None
        self.model_calls = 0
        self.negative_results: dict[str, Any] = {}
        self.global_config_hashes: dict[str, str] = {}
        self._out_fd = None
        self._out_parent_fd = None
        self._out_identity = None

    # -- process helpers ---------------------------------------------------

    def env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["CLAUDE_CONFIG_DIR"] = str(self.cc_home)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if self.owned_entry:
            env["SERVEROS_STATE_DIR"] = self.owned_state_dir
        return env

    def run(self, argv: list[str], *, timeout: int = 180,
            cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
            env=self.env(), cwd=str(cwd or self.session_cwd),
        )

    def record(self, name: str, status: str, detail: str,
               evidence: Any = None) -> None:
        self.results[name] = {"status": status, "detail": detail,
                              "evidence": evidence, "checked_at": _now()}

    # -- steps -------------------------------------------------------------

    def _acquire_output(self) -> None:
        if (not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "geteuid") or
                os.open not in os.supports_dir_fd or os.mkdir not in os.supports_dir_fd):
            raise RuntimeError("exclusive receipt directory ownership is unsupported")
        if any(part.is_symlink() for part in [self.out, *self.out.parents]):
            raise RuntimeError("refused output symlink or ancestor")
        self.out.parent.mkdir(parents=True, exist_ok=True)
        parent = os.open(self.out.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        opened = None
        try:
            # mkdir is the exclusive acquisition, not the earlier exists check.
            os.mkdir(self.out.name, 0o700, dir_fd=parent)
            expected = os.stat(self.out.name, dir_fd=parent, follow_symlinks=False)
            opened = os.open(self.out.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=parent)
            actual = os.fstat(opened)
            if (not stat.S_ISDIR(actual.st_mode) or actual.st_uid != os.geteuid() or
                    actual.st_mode & 0o077 or
                    (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)):
                raise RuntimeError("output ownership or identity changed during acquisition")
            self._out_fd, self._out_parent_fd = opened, parent
            self._out_identity = (actual.st_dev, actual.st_ino, actual.st_uid)
        finally:
            if self._out_fd != opened and opened is not None:
                os.close(opened)
            if self._out_parent_fd != parent:
                os.close(parent)

    def _owns_output(self) -> bool:
        if self._out_fd is None or self._out_parent_fd is None:
            return False
        try:
            if any(part.is_symlink() for part in [self.out, *self.out.parents]):
                return False
            opened = os.fstat(self._out_fd)
            named = os.stat(self.out.name, dir_fd=self._out_parent_fd, follow_symlinks=False)
            parent = os.fstat(self._out_parent_fd)
            parent_path = self.out.parent.stat(follow_symlinks=False)
            return (stat.S_ISDIR(opened.st_mode) and not opened.st_mode & 0o077 and
                    (opened.st_dev, opened.st_ino, opened.st_uid) == self._out_identity and
                    opened.st_uid == os.geteuid() and stat.S_ISDIR(named.st_mode) and
                    (named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino) and
                    (parent.st_dev, parent.st_ino) == (parent_path.st_dev, parent_path.st_ino))
        except OSError:
            return False

    def _write_owned_receipt(self, name: str, data: bytes) -> None:
        if name not in {"RECEIPT.json", "RECEIPT.md"} or not self._owns_output():
            raise RuntimeError("receipt output ownership was not retained")
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=self._out_fd)
        try:
            write_all_fd(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        if not self._owns_output():
            raise RuntimeError("receipt output identity changed during write")
        os.fsync(self._out_fd)

    def close_output(self) -> None:
        for name in ("_out_fd", "_out_parent_fd"):
            fd = getattr(self, name)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)

    def prepare(self) -> None:
        # Fresh, exclusively owned evidence root. Never remove or blend into a
        # directory that already exists; a rerun chooses a new RUN/namespace.
        if self.out.exists() or any(
                part.is_symlink() for part in [self.out, *self.out.parents]
                if part.exists()):
            raise RuntimeError(
                f"refused: {self.out} already exists or an ancestor is a "
                f"symlink; choose a fresh exclusive evidence directory")
        self._acquire_output()
        self.cc_home.mkdir()
        self.session_cwd.mkdir()
        self.logs.mkdir()
        self.tmp.mkdir()
        if not self.claude:
            raise RuntimeError("no `claude` on PATH")
        proc = self.run([self.claude, "--version"], timeout=60)
        self.claude_version = proc.stdout.strip() or proc.stderr.strip()
        if proc.returncode != 0:
            raise RuntimeError(f"`claude --version` failed: {proc.stderr.strip()}")
        self.record("CLAUDE_VERSION", "OBSERVED", self.claude_version,
                    {"exit": proc.returncode})
        self.record("GIT_HEAD", "OBSERVED", self._git_head(),
                    {"worktree": str(ROOT)})
        self.global_config_hashes = self._config_hashes()

    def _config_hashes(self) -> dict[str, str]:
        """Hash the user-level config files, never their contents.

        The runner's Claude Code calls use a fresh `CLAUDE_CONFIG_DIR`, so these
        files must not change. Hashing is the cheap check; the values are not
        copied into the receipt. `~/.claude/.credentials.json` is deliberately
        not touched: it is the credential store, not a config file.
        """
        home = Path.home()
        hashes: dict[str, str] = {}
        for label, path in (("claude_json", home / ".claude.json"),
                            ("settings_json", home / ".claude" / "settings.json")):
            try:
                hashes[label] = _sha256(path)
            except OSError:
                hashes[label] = "ABSENT"
        return hashes

    def _git_head(self) -> str:
        proc = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=30)
        return proc.stdout.strip() if proc.returncode == 0 else "UNKNOWN"

    def build(self) -> None:
        # No removal here: the generator validates the owned entry/config and
        # the empty output before it writes a byte. Deleting an existing
        # `plugin/` would destroy a caller's directory before that validation.
        if self.owned_entry:
            record = cc_builder.generate(
                skills_source=SHARED_SKILLS, out=self.plugin,
                transport="stdio-owner", owned_entry=self.owned_entry,
                owned_config=self.owned_config)
            self._interpose_binding()
            (self.logs / "owned-plugin-build.json").write_text(
                json.dumps(record, indent=2) + "\n", encoding="utf-8")
            self.record("PLUGIN_BUILD", "PASS", "REAL_OWNED_STDIO",
                        {"skills": record["skills"], "transport": record["transport"],
                         "plugin_tree_sha256": _tree_sha256(self.plugin)})
            return
        record = fixture.generate(out=self.plugin)
        self._interpose_binding()
        (self.logs / "fixture-build.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8")
        self.record("FIXTURE_BUILD", "PASS", "TEST_FIXTURE_READY",
                    {"skills": record["skills"], "server": SERVER_NAME,
                     "principal": record["principal"],
                     "plugin_tree_sha256": _tree_sha256(self.plugin)})

    def _interpose_binding(self) -> None:
        self.interposer_log = self.logs / "cc-client-wire.jsonl"
        path = self.plugin / ".mcp.json"
        binding = json.loads(path.read_text(encoding="utf-8"))
        server = binding["mcpServers"][self.server_name]
        if server.get("type") != "stdio":
            raise RuntimeError("client wire observer requires the configured stdio child")
        command = [server["command"], *server.get("args", [])]
        server["command"] = sys.executable
        server["args"] = [str(HERE / "stdio_interposer.py"), "--log",
                          str(self.interposer_log), "--", *command]
        path.write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")

    def package_load(self) -> None:
        validate = self.run([self.claude, "plugin", "validate",
                             str(self.plugin), "--strict"], timeout=120)
        (self.logs / "validate.log").write_text(
            validate.stdout + validate.stderr, encoding="utf-8")
        details = self.run([self.claude, "--plugin-dir", str(self.plugin),
                            "plugin", "details", self.plugin_name], timeout=120)
        (self.logs / "plugin-details.log").write_text(
            details.stdout + details.stderr, encoding="utf-8")
        text = details.stdout + details.stderr
        expected_skills = len(list(fixture.SHARED_SKILLS.glob("*/SKILL.md")))
        skills = f"Skills ({expected_skills})" in text
        mcp = "MCP servers (1)" in text and self.server_name in text
        ok = validate.returncode == 0 and skills and mcp
        self.record(
            "PLUGIN_LOAD_OK", "PASS" if ok else "FAIL",
            f"validate --strict exit 0; details list {expected_skills} Skills and 1 MCP server"
            if ok else "package load did not show the expected inventory",
            {"validate_exit": validate.returncode,
             "skills_count": skills, "expected_skill_count": expected_skills,
             "mcp_one": mcp})

    def mcp_list(self) -> bool:
        """Only successful same-connection/request responses establish ACK/list."""
        proc = self.run([self.claude, "--plugin-dir", str(self.plugin),
                         "mcp", "list"], timeout=180)
        text = proc.stdout + proc.stderr
        (self.logs / "mcp-list.log").write_text(text, encoding="utf-8")
        connected = (proc.returncode == 0 and "Connected" in text
                     and self.qualified_server in text)
        rows = _read_json_lines(self.interposer_log)
        (self.logs / "mcp-list-wire.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8")
        methods = [row.get("method") for row in rows
                   if row.get("direction") == "cc->owned" and row.get("method")]
        invalid = any(row.get("event") in {"observation_failed", "unparseable"} for row in rows)
        connections = {}
        responses = []
        def key(value):
            return (type(value).__name__, value) if type(value) in (str, int) else None
        for row in rows:
            connection = row.get("connection_id")
            if not isinstance(connection, str) or not connection:
                continue
            state = connections.setdefault(connection, {"initialize": None, "ack": False,
                "initialized": False, "tools": None, "count": None})
            direction, method, request = row.get("direction"), row.get("method"), key(row.get("id"))
            if direction == "cc->owned":
                if method == "initialize" and request is not None:
                    state.update(initialize=request, ack=False, initialized=False, tools=None, count=None)
                elif method == "notifications/initialized" and state["ack"]:
                    state["initialized"] = True
                elif method == "tools/list" and request is not None and state["ack"] and state["initialized"]:
                    state.update(tools=request, count=None)
            elif direction == "owned->cc" and method is None:
                success = row.get("response_ok") is True and "error" not in row and "error_code" not in row
                if row.get("request_method") == "initialize" and request == state["initialize"] and request is not None:
                    state["ack"] = success
                    responses.append(row)
                elif (row.get("request_method") == "tools/list" and request == state["tools"]
                      and request is not None and state["ack"] and state["initialized"]):
                    count = row.get("tool_count")
                    state["count"] = count if success and type(count) is int and count >= 0 else None
                    responses.append(row)
        observed = not invalid and proc.returncode == 0
        handshake = observed and any(state["ack"] and state["initialized"] for state in connections.values())
        counts = [state["count"] for state in connections.values()
                  if state["ack"] and state["initialized"] and state["count"] is not None]
        count = counts[-1] if observed and counts else None
        listed = handshake and count is not None and count > 0
        self.record("CC_CLI_CONNECTED", "PASS" if connected else "FAIL",
                    "Claude Code mcp list connection text (separate from wire ACK)",
                    {"qualified_server": self.qualified_server, "exit": proc.returncode})
        evidence = {"methods": methods, "wire": str(self.interposer_log) if self.interposer_log else None,
                    "source": "CC correlated interposed stdio wire", "responses": responses,
                    "observation_failed": invalid}
        self.record("MCP_INITIALIZE_OK", "PASS" if handshake else "FAIL" if rows else "NOT_RUN",
                    "successful initialize result and initialized on the same connection"
                    if handshake else "no successful correlated client handshake", evidence)
        self.record("TOOLS_LIST_OK", "PASS" if listed else "FAIL" if rows else "NOT_RUN",
                    f"correlated client tools/list returned {count} tools" if listed
                    else "no successful correlated tools/list after the handshake",
                    {**evidence, "tool_count": count})
        return connected and handshake and listed

    def plugin_metadata(self) -> None:
        """The manifest keys and the Skills bytes, checked structurally.

        This is the `L0_CODE` layer: it reads the generated directory and the
        one canonical Skills source. It says nothing about whether a client
        loaded the plugin or whether any tool exists.
        """
        manifest = json.loads(
            (self.plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        stray = sorted(set(manifest) - DOCUMENTED_MANIFEST_KEYS)
        generated = sorted(p.parent.name for p in (self.plugin / "skills").glob("*/SKILL.md"))
        source = sorted(p.parent.name for p in SHARED_SKILLS.glob("*/SKILL.md"))
        identical = generated == source and all(
            _sha256(self.plugin / "skills" / name / "SKILL.md")
            == _sha256(SHARED_SKILLS / name / "SKILL.md")
            for name in source)
        binding = json.loads((self.plugin / ".mcp.json").read_text(encoding="utf-8"))
        server = (binding.get("mcpServers") or {}).get(self.server_name) or {}
        binding_ok = (server.get("type") == "stdio"
                      and "url" not in server and "oauth" not in server
                      and "command" in server)
        ok = (manifest.get("name") == self.plugin_name and not stray
              and identical and binding_ok)
        self.record(
            "PLUGIN_METADATA", "PASS" if ok else "FAIL",
            "documented manifest keys, the shared Skills byte-identical, stdio binding"
            if ok else "metadata, Skills bytes or binding did not match",
            {"manifest_keys": sorted(manifest), "stray_keys": stray,
             "skills_generated": generated, "skills_source": source,
             "binding_type": server.get("type")})
        self.record(
            "SKILLS_BYTES", "PASS" if identical else "FAIL",
            f"{len(source)}/{len(source)} SKILL.md byte-identical to the canonical source"
            if identical else "a SKILL.md was re-typed",
            {"sha256": {name: _sha256(self.plugin / "skills" / name / "SKILL.md")
                        for name in generated}})

    def cli_no_side_effect(self) -> None:
        """No-side-effect CLI commands, under the isolated config directory."""
        mcp_get = self.run([self.claude, "--plugin-dir", str(self.plugin),
                            "mcp", "get", self.qualified_server], timeout=120)
        (self.logs / "mcp-get.log").write_text(
            mcp_get.stdout + mcp_get.stderr, encoding="utf-8")
        listed = self.run([self.claude, "plugin", "list"], timeout=60)
        (self.logs / "plugin-list.log").write_text(
            listed.stdout + listed.stderr, encoding="utf-8")
        inline_not_installed = self.plugin_name not in (listed.stdout + listed.stderr)
        after = self._config_hashes()
        unchanged = after == self.global_config_hashes
        ok = (mcp_get.returncode == 0 and listed.returncode == 0
              and inline_not_installed)
        self.record(
            "CLI_NO_SIDE_EFFECT", "PASS" if ok else "FAIL",
            "mcp get and plugin list exit 0; the inline fixture is not installed"
            if ok else "a no-side-effect CLI command failed or the fixture appeared installed",
            {"mcp_get_exit": mcp_get.returncode,
             "plugin_list_exit": listed.returncode,
             "inline_not_installed": inline_not_installed})
        self.record(
            "GLOBAL_CONFIG", "OBSERVED",
            "user config hashes unchanged during the run" if unchanged
            else "user config hashes differ; another process may have written them",
            {"before": self.global_config_hashes, "after": after,
             "unchanged": unchanged, "credentials_file_touched": False})

    def model_readbacks(self) -> None:
        """One real `claude -p` session, restricted to three read tools."""
        if not self.want_model_readbacks:
            self.record("CLIENT_E2E", "NOT_RUN",
                        "pass --model-readbacks to run the real model readback",
                        {"model_calls": 0})
            return
        if self.owned_entry:
            self.record("CLIENT_E2E", "NOT_RUN",
                        "owned mode uses a real runtime; no paid model turn was requested",
                        {"model_calls": 0})
            return
        wire = self.plugin / ".fixture-state" / "mcp-wire.log"
        before = len(_read_json_lines(wire))
        prompt = (
            "You are testing a LOCAL TEST FIXTURE of the ServerOS Fleet MCP "
            "surface. The device is invented (`device:fixture-node`); nothing "
            "here touches a real machine. Call exactly these three MCP tools, "
            "in this order, and do not call any other tool:\n"
            "1. fleet_list_devices\n"
            "2. fleet_list_sessions\n"
            "3. fleet_cloudcli_native_capability with "
            "{\"device_ref\":\"device:fixture-node\"}\n"
            "Then reply with a short table of what each returned. Do not "
            "retry a failed call and do not attempt any write."
        )
        allowed = [TOOL_PREFIX + name for name in READ_TOOLS]
        argv = [
            self.claude, "-p", prompt,
            "--plugin-dir", str(self.plugin),
            "--allowedTools", *allowed,
            "--output-format", "json",
            "--max-turns", "8",
            "--permission-mode", "default",
        ]
        try:
            proc = self.run(argv, timeout=300)
        except subprocess.TimeoutExpired as exc:
            self.record("CLIENT_E2E", "BLOCKED",
                        f"model readback timed out: {exc}",
                        {"model_calls": self.model_calls})
            return
        self.model_calls += 1
        (self.logs / "model-stdout.json").write_text(proc.stdout, encoding="utf-8")
        (self.logs / "model-stderr.log").write_text(proc.stderr, encoding="utf-8")
        payload = _json_from_output(proc.stdout) or {}
        denials = payload.get("permission_denials") or []
        rows = _read_json_lines(wire)[before:]
        (self.logs / "model-wire.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8")
        calls: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row.get("event") == "response" and row.get("method") == "tools/call":
                calls[row.get("tool")] = row
        missing = [name for name in READ_TOOLS if name not in calls]
        errored = [name for name in READ_TOOLS
                   if name in calls and calls[name].get("isError")]
        ok = (proc.returncode == 0 and not denials and not missing and not errored)
        detail = (
            "the real client called all three read tools and each returned a "
            "non-error result"
            if ok else
            f"returncode={proc.returncode}; denials={len(denials)}; "
            f"missing={missing}; errored={errored}")
        self.record("CLIENT_E2E", "PASS" if ok else "FAIL",
                    detail,
                    {"model_calls": self.model_calls,
                     "returncode": proc.returncode,
                     "permission_denials": [d.get("tool_name") for d in denials],
                     "tool_calls": {name: calls.get(name) for name in READ_TOOLS},
                     "total_cost_usd": payload.get("total_cost_usd"),
                     "result": payload.get("result")})

    def negative_cases(self) -> None:
        """Deterministic protocol calls; no model, no network."""
        state = self.out / "negative-state"
        log = self.logs / "negative-wire.jsonl"
        for path in (log,):
            if path.exists():
                path.unlink()
        argv = [sys.executable, str(fixture.DEFAULT_SERVER),
                "--log", str(log), "--state-dir", str(state),
                "--principal", "read-only"]
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert proc.stdin and proc.stdout

        def rpc(method: str, params: dict | None = None,
                *, expect_response: bool = True) -> dict[str, Any]:
            message = {"jsonrpc": "2.0", "id": f"t-{method}",
                       "method": method, "params": params or {}}
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
            if not expect_response:
                return {}
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError(f"fixture closed stdout after {method}")
            return json.loads(line)

        results: dict[str, Any] = {}
        try:
            init = rpc("initialize", {"protocolVersion": "2025-06-18",
                                      "capabilities": {},
                                      "clientInfo": {"name": "e2e", "version": "0"}})
            results["initialize"] = init.get("result", {}).get("serverInfo")
            rpc("notifications/initialized", expect_response=False)
            listed = rpc("tools/list", {}).get("result", {}).get("tools", [])
            names = [tool.get("name") for tool in listed]
            results["tools_list"] = {"count": len(names),
                                     "has_send": "fleet_send_to_session" in names,
                                     "has_capability":
                                         "fleet_cloudcli_native_capability" in names}

            def call(name: str, arguments: dict) -> dict[str, Any]:
                response = rpc("tools/call", {"name": name, "arguments": arguments})
                return response.get("result") or {}

            status = call("fleet_status", {})
            status_body = status.get("structuredContent") or {}
            results["status"] = {
                "isError": status.get("isError"),
                "principal": status_body.get("principal"),
            }
            devices = call("fleet_list_devices", {})
            devices_body = devices.get("structuredContent") or {}
            results["list_devices"] = {
                "isError": devices.get("isError"),
                "total": devices_body.get("total"),
                "device_refs": [row.get("device_ref")
                                for row in devices_body.get("devices") or []],
                "provider_detail": ((devices_body.get("devices") or [{}])[0]
                                    .get("provider") or {}).get("detail"),
            }
            sessions = call("fleet_list_sessions", {})
            sessions_body = sessions.get("structuredContent") or {}
            results["list_sessions"] = {
                "isError": sessions.get("isError"),
                "total": sessions_body.get("total"),
                "session_refs": [row.get("session_ref")
                                 for row in sessions_body.get("sessions") or []],
            }
            capability = call("fleet_cloudcli_native_capability",
                              {"device_ref": DEVICE})
            capability_body = capability.get("structuredContent") or {}
            results["capability"] = {
                "isError": capability.get("isError"),
                "state": capability_body.get("state"),
                "observed_count": capability_body.get("observed_count"),
                "sessions_list_state": (capability_body.get("tools") or {})
                .get("sessions_list", {}).get("state"),
            }
            send = call("fleet_send_to_session",
                        {"session_ref": SESSION_REF, "message": "must be refused"})
            send_body = send.get("structuredContent") or {}
            results["send_refused"] = {
                "isError": send.get("isError"),
                "code": send_body.get("code"),
            }
            bad_ref = call("fleet_get_session",
                           {"session_ref": "fixture-session-0001"})
            results["bad_session_ref"] = {
                "isError": bad_ref.get("isError"),
                "code": (bad_ref.get("structuredContent") or {}).get("code"),
            }
            unknown = call("fleet_nope", {})
            results["unknown_tool"] = {
                "isError": unknown.get("isError"),
                "code": (unknown.get("structuredContent") or {}).get("code"),
            }
            widened = call("fleet_list_sessions", {"device_ref": DEVICE})
            results["widened_filter"] = {
                "isError": widened.get("isError"),
                "code": (widened.get("structuredContent") or {}).get("code"),
            }
        finally:
            proc.stdin.close()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            stderr = proc.stderr.read() if proc.stderr else ""
            (self.logs / "negative-stderr.log").write_text(stderr, encoding="utf-8")

        rows = _read_json_lines(log)
        shutdown = next((row for row in rows if row.get("event") == "shutdown"), {})
        results["provider_sends"] = shutdown.get("provider_sends")
        (self.logs / "negative-results.json").write_text(
            json.dumps(results, indent=2) + "\n", encoding="utf-8")

        checks = {
            "status_principal_read_only": (
                results["status"]["principal"] or {}).get("scopes") == ["fleet:read"],
            "discovery_has_write_tool": results["tools_list"]["has_send"],
            "list_devices_ok": results["list_devices"]["isError"] is False
                and results["list_devices"]["total"] == 1,
            "list_sessions_ok": results["list_sessions"]["isError"] is False
                and results["list_sessions"]["total"] == 1,
            "capability_observed": results["capability"]["isError"] is False
                and results["capability"]["state"] == "OBSERVED"
                and results["capability"]["sessions_list_state"] == "USABLE",
            "send_refused_scope": results["send_refused"]["isError"] is True
                and results["send_refused"]["code"] == "SCOPE_DENIED",
            "provider_not_reached": results["provider_sends"] == 0,
            "bad_session_ref": results["bad_session_ref"]["code"] == "BAD_SESSION_REF",
            "unknown_tool": results["unknown_tool"]["code"] == "BAD_REQUEST",
            "widened_filter": results["widened_filter"]["code"] == "BAD_REQUEST",
        }
        (self.logs / "negative-checks.json").write_text(
            json.dumps(checks, indent=2) + "\n", encoding="utf-8")
        ok = all(checks.values())
        self.negative_results = results
        self.record("AUTH_SCOPE", "PASS" if ok else "FAIL",
                    "read-only principal: 33 tools discovered, write refused "
                    "SCOPE_DENIED, provider never reached"
                    if ok else f"failed: {[k for k, v in checks.items() if not v]}",
                    {"checks": checks, "results": results})

    def temp_daemon_facts(self) -> None:
        """The local stdio daemon, restricted to pre-approved virtual facts.

        This is the same process the negative tests use. It is a **temporary
        daemon**, not `serverosd`, and its device and session are constants in
        `fixture_stdio_server.py`. Nothing here contacts a real machine.
        """
        results = self.negative_results
        sends = results.get("provider_sends")
        refused = (results.get("send_refused") or {}).get("code")
        tools_count = (results.get("tools_list") or {}).get("count")
        devices_total = (results.get("list_devices") or {}).get("total")
        sessions_total = (results.get("list_sessions") or {}).get("total")
        capability_state = (results.get("capability") or {}).get("state")
        ok = (tools_count == 33 and devices_total == 1 and sessions_total == 1
              and capability_state == "OBSERVED" and sends == 0)
        self.record(
            "TEMP_DAEMON_VIRTUAL_FACTS", "PASS" if ok else "FAIL",
            "local stdio daemon: 33 tools, one virtual device and session, "
            "OBSERVED capability" if ok else
            "the local temporary daemon did not answer the virtual facts",
            {"virtual_facts": True, "real_device": False,
             "serverosd_started": False, "device_ref": DEVICE,
             "session_ref": SESSION_REF, "tools_count": tools_count,
             "devices_total": devices_total, "sessions_total": sessions_total,
             "capability_state": capability_state, "provider_sends": sends})
        self.record(
            "SEND_SCOPE_DENIED",
            "PASS" if refused == "SCOPE_DENIED" and sends == 0 else "FAIL",
            "fleet_send_to_session refused SCOPE_DENIED and the fake provider "
            "saw 0 sends" if refused == "SCOPE_DENIED" and sends == 0 else
            "the send was not refused at the scope before the provider",
            {"code": refused, "provider_sends": sends})

    # -- receipt -----------------------------------------------------------

    def owned_readback(self) -> None:
        """Read the real owned runtime; never send/create."""
        if not self.owned_entry:
            return
        result = fleet_owned_probe.probe(
            entry=Path(self.owned_entry), config=Path(self.owned_config),
            state_dir=Path(self.owned_state_dir), cwd=self.session_cwd,
            timeout=60, tmpdir=self.tmp)
        self.owned_result = result
        (self.logs / "owned-readback.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        project = result.get("project_create_schema") or {}
        required = set((project.get("inputSchema") or {}).get("required") or [])
        self.record("OWNED_PROBE_READBACK",
                    "PASS" if (result["tool_count_actual"] >= 34
                               and result["project_create_present"]
                               and result["six_scopes"]
                               and result["durability"] == "AGENT_CONTROL_STORE"
                               and {"claude", "codex"}
                               <= set(result["owner_access_families"])
                               and result["session_read"] is not None)
                    else "FAIL",
                    "real owned stdio read-only",
                    {k: result.get(k) for k in
                     ("server_name", "server_version", "tool_count_actual",
                      "project_create_present", "scopes", "six_scopes",
                      "durability", "owner_access_families", "sessions_total",
                      "tokyo_sessions")})
        self.record("OWNED_PROBE_PROJECT_CAPABILITY",
                    "PASS" if result["project_create_present"]
                    and {"device", "root", "name"} <= required else "FAIL",
                    "fleet_create_project schema read from tools/list",
                    {"required": sorted(required)})
        self.record("OWNED_PROBE_AUTH_SCOPE",
                    "PASS" if result["six_scopes"] else "FAIL",
                    "the owned principal read back the six scopes",
                    {"scopes": result["scopes"], "six_scopes": result["six_scopes"]})

    def write_receipt(self) -> bool:
        if not self._owns_output():
            return False
        owned = bool(self.owned_entry)
        owned_result = self.owned_result or {}
        owned_surface = owned_result.get("status", {}).get("surface") or {}
        device = (owned_surface.get("local_device_ref")
                  if owned else "device:fixture-node (TEST_FIXTURE, invented)")
        payload = {
            "schema": "serveros/fleet-cc-client-e2e-receipt.v1",
            "checked_at": _now(),
            "git_head": self._git_head(),
            "claude_version": self.claude_version,
            "evidence_level": "LOCAL_REAL_OWNED" if owned else "LOCAL",
            "scope": {
                "transport": "stdio-owner" if owned else "stdio",
                "device": device,
                "session": None if owned else SESSION_REF,
                "network": "local",
                "real_device": bool(owned),
                "real_device_readback": bool(owned),
                "real_device_actions": False,
                "serverosd_started": False,
                "credential_read": False,
                "credential_file_read": False,
                "credential_env_values_recorded": False,
                "login_copied": False,
                "global_config_modified": not (
                    self.results.get("GLOBAL_CONFIG", {}).get("evidence") or {}
                ).get("unchanged", False),
                "model_calls": self.model_calls,
                "client_wire_frames": bool(
                    (self.results.get("MCP_INITIALIZE_OK", {}).get("evidence")
                     or {}).get("methods")),
            },
            "layers": {
                "L0_CODE": {name: self.results.get(name, {}).get("status")
                            for name in (("PLUGIN_BUILD" if owned else "FIXTURE_BUILD"),
                                         "PLUGIN_METADATA", "SKILLS_BYTES")},
                "L1_LOCAL_CLI": {name: self.results.get(name, {}).get("status")
                                 for name in ("CLAUDE_VERSION", "PLUGIN_LOAD_OK",
                                              "CLI_NO_SIDE_EFFECT")},
                ("L2_OWNED_RUNTIME_CLIENT_WIRE" if owned
                 else "L2_TEMP_DAEMON"): (
                    {name: self.results.get(name, {}).get("status")
                     for name in ("CC_CLI_CONNECTED", "MCP_INITIALIZE_OK",
                                  "TOOLS_LIST_OK")}
                    if owned else
                    {name: self.results.get(name, {}).get("status")
                     for name in ("TEMP_DAEMON_VIRTUAL_FACTS",
                                  "SEND_SCOPE_DENIED")}),
                **({"L2_OWNED_PROBE_SEPARATE": {
                    name: self.results.get(name, {}).get("status")
                    for name in ("OWNED_PROBE_READBACK",
                                 "OWNED_PROBE_PROJECT_CAPABILITY",
                                 "OWNED_PROBE_AUTH_SCOPE")}}
                   if owned else {}),
                "L3_CLIENT_E2E_MODEL": {
                    "CLIENT_E2E": self.results.get("CLIENT_E2E", {}).get("status")},
            },
            "claim_sources": {
                "CLAUDE_VERSION/PLUGIN_LOAD_OK/CLI_NO_SIDE_EFFECT":
                    "real Claude Code CLI on this host",
                "CC_CLI_CONNECTED": "claude mcp list text + qualified server",
                "MCP_INITIALIZE_OK":
                    "CC same-connection/id successful initialize response + initialized",
                "TOOLS_LIST_OK":
                    "CC same-connection/id successful tools/list response + tool count",
                "OWNED_PROBE_*":
                    "fleet_owned_probe.py separate read-only chain; never a "
                    "client handshake or tool-call claim",
                "CLIENT_E2E": "real model turn only; otherwise NOT_RUN",
            },
            "MODEL_SKILL_BEHAVIOR": "NOT_RUN",
            "results": self.results,
        }
        self._write_owned_receipt("RECEIPT.json",
            (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))

        lines = [
            ("# Claude Code client E2E receipt — real owned stdio runtime"
             if owned else "# Claude Code client E2E receipt — local stdio fixture"),
            "",
            f"- checked: `{payload['checked_at']}`",
            f"- git head: `{payload['git_head']}`",
            f"- claude: `{self.claude_version}`",
            f"- evidence level: **{payload['evidence_level']}**",
            f"- device: `{device}`",
            "- credential read: no; global config modified: no",
            "",
            "| check | status | detail |",
            "|---|---|---|",
        ]
        for name, row in self.results.items():
            detail = str(row["detail"]).replace("|", "\\|")
            lines.append(f"| `{name}` | **{row['status']}** | {detail} |")
        lines += ["", "## What this receipt is not", ""]
        if owned:
            lines += [
                "`LOCAL_REAL_OWNED` means a real Claude Code client on this host",
                "loading the real owned stdio runtime and reading the real local",
                "device. It performs no send/create and no model turn. It is not a",
                "production deployment or a model-behaviour proof.",
            ]
        else:
            lines += [
                "`LOCAL` means a real Claude Code client on this host, over local",
                "stdio, against a fixture device. It is not `REAL_DEVICE`, not the",
                "live Fleet Next ingress, and not `PRODUCTION`. `TOOLS_LIST_OK` is",
                "not an authenticated product `tools/list`; `AUTH_SCOPE` is the",
                "fixture principal's `fleet:read`, not a real token's grants.",
            ]
        lines += [""]
        self._write_owned_receipt("RECEIPT.md", "\n".join(lines).encode("utf-8"))
        return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model-readbacks", action="store_true",
                        help="run one real claude -p session for read-only tools")
    parser.add_argument("--owned-entry")
    parser.add_argument("--owned-config")
    parser.add_argument("--owned-state-dir",
                        default="/data/home/vince/state/serveros")
    args = parser.parse_args(argv)

    # Refuse before constructing a Runner: a receipt must never be written
    # into a directory that already belongs to someone else.
    if args.out.exists() or args.out.is_symlink() or any(
            part.is_symlink() for part in [args.out, *args.out.parents]
            if part.exists()):
        print(f"refused: {args.out} already exists or an ancestor is a symlink; "
              f"choose a fresh evidence directory", file=sys.stderr)
        return 2

    runner = Runner(args.out, args.model_readbacks,
                    owned_entry=args.owned_entry,
                    owned_config=args.owned_config,
                    owned_state_dir=args.owned_state_dir)
    try:
        runner.prepare()
        runner.build()
        runner.plugin_metadata()
        runner.package_load()
        if args.owned_entry:
            runner.owned_readback()
        runner.mcp_list()
        runner.cli_no_side_effect()
        if not args.owned_entry:
            runner.negative_cases()
            runner.temp_daemon_facts()
        runner.model_readbacks()
    except Exception as exc:  # noqa: BLE001 - a runner failure is the receipt
        runner.record("RUNNER", "BLOCKED", f"{type(exc).__name__}: {exc}")
        try:
            runner.write_receipt()  # False means acquisition/identity was lost.
        except Exception as receipt_error:
            print(f"RECEIPT_REFUSED: {type(receipt_error).__name__}", file=sys.stderr)
        finally:
            runner.close_output()
        print(f"BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    try:
        if not runner.write_receipt():
            print("RECEIPT_REFUSED: output ownership was lost", file=sys.stderr)
            return 2
    except Exception as exc:
        print(f"RECEIPT_REFUSED: {type(exc).__name__}", file=sys.stderr)
        return 2
    finally:
        runner.close_output()
    statuses = {name: row["status"] for name, row in runner.results.items()}
    for name in ("PLUGIN_METADATA", "SKILLS_BYTES", "PLUGIN_LOAD_OK",
                 "CLI_NO_SIDE_EFFECT", "MCP_INITIALIZE_OK", "TOOLS_LIST_OK",
                 "TEMP_DAEMON_VIRTUAL_FACTS", "SEND_SCOPE_DENIED",
                 "AUTH_SCOPE", "CC_CLI_CONNECTED", "OWNED_PROBE_READBACK",
                 "OWNED_PROBE_PROJECT_CAPABILITY", "OWNED_PROBE_AUTH_SCOPE",
                 "CLIENT_E2E"):
        print(f"{name}: {statuses.get(name, 'NOT_RUN')}")
    failed = [name for name, status in statuses.items()
              if status == "FAIL" and (name != "CLIENT_E2E" or args.model_readbacks)]
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
