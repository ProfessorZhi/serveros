#!/usr/bin/env python3
"""Drive a real Codex CLI against the fixture or the real owned stdio runtime — no model.

Codex 0.160.0 is started with an isolated `CODEX_HOME`, a local marketplace and
plugin, then the app-server protocol performs initialize, thread/start,
mcpServerStatus/list and one read-only mcpServer/tool/call. This is a real CLI
load and protocol handshake, not a model call.

The receipt keeps separate facts: plugin install, MCP connection, tools/list,
tool call, shared-Skill bytes, fixture-versus-real-owned boundary and
model-turn count. With `--owned-entry/--owned-config`, the MCP entry is the real
`bin/serveros-fleet-owned --stdio --config PATH`, and a read-only probe records
the actual 34-tool surface, project-create schema, six scopes, durability and
session readback.
The plugin id reported by Codex is its local marketplace selector; it is not an
App/Creator backend id.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parent

import importlib.util
sys.path.insert(0, str(HERE))
import fleet_owned_probe  # noqa: E402
from stdio_framing import FramingError, LineFramer, write_all_fd  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "fleet_codex_fixture_runner", HERE / "build_codex_local_fixture.py")
_fixture = importlib.util.module_from_spec(_spec)
sys.modules["fleet_codex_fixture_runner"] = _fixture
_spec.loader.exec_module(_fixture)

SHARED = ROOT / "fleet-v1-first-install" / "plugin" / "skills"
SERVER = _fixture.SERVER_NAME
FIXTURE_NAME = _fixture.FIXTURE_NAME
MARKET_NAME = _fixture.MARKET_NAME

#: No model turn is requested, so provider credentials are neither needed nor
#: inherited. Only their names are recorded; values are never read or copied.
CREDENTIAL_ENV_KEYS = (
    "OPENAI_API_KEY", "CODEX_API_KEY",
    "OPENAI_ACCESS_TOKEN", "CODEX_ACCESS_TOKEN",
)


def _client_env(home: Path, tmpdir: Path) -> dict[str, str]:
    """An explicit, isolated environment for the real Codex CLI."""
    env = {k: v for k, v in os.environ.items() if k not in CREDENTIAL_ENV_KEYS}
    env["CODEX_HOME"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TMPDIR"] = str(tmpdir)
    return env


class CodexServer:
    def __init__(self, codex: str, home: Path, cwd: Path, timeout: float,
                 tmpdir: Path | None = None):
        self.timeout = timeout
        self.notifications: list[dict] = []
        self.notification_bytes = 0
        self.max_notifications = 1024
        self.max_notification_bytes = 4 * 1024 * 1024
        self.tmpdir = Path(tmpdir) if tmpdir is not None else Path(cwd) / "tmp"
        env = _client_env(home, self.tmpdir)
        self.proc = subprocess.Popen(
            [codex, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=False, bufsize=0,
            cwd=str(cwd), env=env)
        # Built on the first request, so constructing the runner does not
        # require a real child (and so a test can inspect the environment).
        self.reader: LineFramer | None = None

    def send(self, message: dict) -> None:
        assert self.proc.stdin is not None
        write_all_fd(self.proc.stdin.fileno(),
                     (json.dumps(message) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, request_id: int, method: str, params: dict) -> dict:
        self.send({"jsonrpc": "2.0", "id": request_id,
                   "method": method, "params": params})
        if self.reader is None:
            self.reader = LineFramer(self.proc.stdout)
        end = time.monotonic() + self.timeout
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"{method} did not answer within {self.timeout}s")
            try:
                line = self.reader.next_line(remaining)
            except EOFError as exc:
                raise RuntimeError("codex app-server closed its stdout") from exc
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(message, dict):
                raise FramingError("app-server frame is not a JSON object")
            if type(message.get("id")) is type(request_id) and message.get("id") == request_id:
                return message
            if (len(self.notifications) >= self.max_notifications or
                    self.notification_bytes + len(line) > self.max_notification_bytes):
                raise FramingError("notification retention exceeds its count/bytes budget")
            self.notifications.append(message)
            self.notification_bytes += len(line)

    def close(self) -> str:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        stderr = self.proc.stderr.read() if self.proc.stderr else b""
        return stderr.decode("utf-8", errors="replace")


def _run(cmd: list[str], *, env: dict, cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout)


def _git_head() -> str:
    proc = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                          capture_output=True, text=True, timeout=30)
    return proc.stdout.strip() if proc.returncode == 0 else "UNKNOWN"


def _skills_equal(installed_root: Path) -> tuple[bool, list[str]]:
    shared = {p.parent.name: p for p in SHARED.glob("*/SKILL.md")}
    installed = {p.parent.name: p for p in installed_root.rglob("skills/*/SKILL.md")}
    if set(shared) != set(installed):
        return False, sorted(set(shared) ^ set(installed))
    bad = [name for name in sorted(shared)
           if shared[name].read_bytes() != installed[name].read_bytes()]
    return not bad, bad


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--owned-entry")
    parser.add_argument("--owned-config")
    parser.add_argument("--owned-state-dir",
                        default="/data/home/vince/state/serveros")
    parser.add_argument("--owned-cwd")
    args = parser.parse_args(argv)

    raw_out = Path(args.out)
    if raw_out.is_symlink() or any(
            part.is_symlink() for part in [raw_out, *raw_out.parents]
            if part.exists()):
        print(f"refused: {raw_out} or an ancestor is a symlink; choose a fresh "
              f"exclusive evidence root", file=sys.stderr)
        return 2
    out = raw_out.resolve()
    if out.exists():
        print(f"refused: {out} already exists; choose a fresh evidence directory",
              file=sys.stderr)
        return 2
    out.mkdir(parents=True)
    tmp = out / "tmp"
    tmp.mkdir()
    owned = bool(args.owned_entry)
    if owned:
        fixture = _fixture.generate_owned(
            out=out / "fixture", entry=Path(args.owned_entry),
            config=Path(args.owned_config), state_dir=Path(args.owned_state_dir),
            cwd=Path(args.owned_cwd) if args.owned_cwd else REPO)
    else:
        fixture = _fixture.generate(out=out / "fixture")
    home = out / "codex-home"
    home.mkdir()
    env = _client_env(home, tmp)
    credential_names = sorted(k for k in CREDENTIAL_ENV_KEYS if k in os.environ)

    claims: dict[str, object] = {
        "schema": "serveros/fleet-codex-client-e2e.v1",
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_head": _git_head(),
        "evidence_level": "LOCAL_REAL_OWNED" if owned else "LOCAL_FIXTURE",
        "real_codex_cli": True,
        "real_device_readback": owned,
        "real_device_actions": False,
        "global_config_modified": False,
        "credential_read": False,
        "credential_file_read": False,
        "credential_env_values_recorded": False,
        "credential_env_names_present": credential_names,
        "credential_env_names_removed": credential_names,
        "login_copied": False,
        "codex_home_isolated": True,
        "tmpdir": str(tmp),
        "model_turns_requested": 0,
        "model_calls": 0,
        "model_skill_behavior": "NOT_RUN",
        "fixture_plugin": fixture["fixture_name"],
        "real_owned_entry": owned,
        "fixture_device": not owned,
    }
    failures: list[str] = []
    try:
        version = _run([args.codex, "--version"], env=env, cwd=REPO,
                       timeout=args.timeout)
        claims["codex_version"] = (version.stdout + version.stderr).strip()
        if version.returncode != 0:
            failures.append("CODEX_VERSION")

        add_market = _run([args.codex, "plugin", "marketplace", "add",
                           fixture["marketplace"]], env=env, cwd=REPO,
                          timeout=args.timeout)
        (out / "logs").mkdir(exist_ok=True)
        (out / "logs" / "marketplace-add.log").write_text(
            add_market.stdout + add_market.stderr, encoding="utf-8")
        claims["PLUGIN_INSTALL_OK"] = (
            add_market.returncode == 0
            and _run([args.codex, "plugin", "add",
                      f"{fixture['fixture_name']}@{fixture['marketplace_name']}"], env=env, cwd=REPO,
                     timeout=args.timeout).returncode == 0)
        if not claims["PLUGIN_INSTALL_OK"]:
            failures.append("PLUGIN_INSTALL_OK")
        plugin_list = _run([args.codex, "plugin", "list"], env=env, cwd=REPO,
                           timeout=args.timeout)
        (out / "logs" / "plugin-list.log").write_text(
            plugin_list.stdout + plugin_list.stderr, encoding="utf-8")
        claims["PLUGIN_LISTED"] = (plugin_list.returncode == 0
                                   and fixture["fixture_name"] in plugin_list.stdout)

        skills_ok, skills_bad = _skills_equal(home)
        claims["SKILLS_BYTES"] = skills_ok
        claims["skills_bad"] = skills_bad
        if not skills_ok:
            failures.append("SKILLS_BYTES")

        server = CodexServer(args.codex, home, REPO, args.timeout, tmp)
        stderr = ""
        try:
            init = server.request(1, "initialize", {
                "clientInfo": {"name": "serveros-fleet-probe", "version": "0.0.1"},
                "capabilities": {"experimentalApi": True},
            })
            claims["MCP_INITIALIZE_OK"] = "result" in init and "error" not in init
            if not claims["MCP_INITIALIZE_OK"]:
                failures.append("MCP_INITIALIZE_OK")
            thread = server.request(2, "thread/start", {
                "cwd": str(REPO), "ephemeral": True,
            })
            thread_id = (((thread.get("result") or {}).get("thread") or {}).get("id"))
            claims["THREAD_START_OK"] = bool(thread_id)
            if not thread_id:
                failures.append("THREAD_START_OK")
                raise RuntimeError("no thread id")
            status = server.request(3, "mcpServerStatus/list", {
                "serverName": SERVER, "detail": "full",
                "threadId": thread_id,
            })
            rows = (status.get("result") or {}).get("data") or []
            row = next((r for r in rows if r.get("name") == SERVER), {})
            tools = row.get("tools") or {}
            claims["MCP_SERVER"] = SERVER
            claims["MCP_CONNECTED"] = row.get("runtimeStatus") == "connected"
            claims["server_info"] = row.get("serverInfo")
            claims["plugin_id"] = row.get("pluginId")
            claims["plugin_id_note"] = (
                "local Codex marketplace selector, not an App/Creator backend id")
            claims["TOOLS_LIST_OK"] = bool(tools) and claims["MCP_CONNECTED"]
            claims["tool_count_actual"] = len(tools)
            claims["tool_names"] = sorted(tools)
            project_tool = tools.get("fleet_create_project") or {}
            claims["project_create_capability_actual"] = bool(project_tool)
            claims["project_create_schema"] = project_tool
            claims["TOOLS_LIST_SOURCE"] = "app-server mcpServerStatus/list"
            if not claims["TOOLS_LIST_OK"]:
                failures.append("TOOLS_LIST_OK")

            call = server.request(4, "mcpServer/tool/call", {
                "server": SERVER, "threadId": thread_id,
                "tool": "fleet_list_devices", "arguments": {},
            })
            result = call.get("result") or {}
            claims["TOOL_CALL_OK"] = (
                "error" not in call and result.get("isError") is not True
                and bool(result.get("content")))
            claims["TOOL_CALL_SOURCE"] = "app-server mcpServer/tool/call"
            if not claims["TOOL_CALL_OK"]:
                failures.append("TOOL_CALL_OK")
        finally:
            stderr = server.close()
        claims["bubblewrap_warning_observed"] = "bubblewrap" in stderr.lower()
        claims["model_provider_401_observed"] = "401 Unauthorized" in stderr
        (out / "logs" / "app-server-stderr.txt").write_text(stderr, encoding="utf-8")

        if owned:
            readback = fleet_owned_probe.probe(
                entry=Path(args.owned_entry), config=Path(args.owned_config),
                state_dir=Path(args.owned_state_dir),
                cwd=Path(args.owned_cwd) if args.owned_cwd else REPO,
                timeout=args.timeout, tmpdir=tmp)
            (out / "logs" / "owned-readback.json").write_text(
                json.dumps(readback, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")
            claims["owned_probe"] = readback
            claims["PROBE_TOOL_COUNT_ACTUAL"] = readback["tool_count_actual"]
            claims["PROBE_PROJECT_CREATE_PRESENT"] = readback["project_create_present"]
            claims["OWNED_PROBE_OK"] = bool(
                readback["tool_count_actual"] >= 34
                and readback["project_create_present"]
                and readback["six_scopes"]
                and readback["durability"] == "AGENT_CONTROL_STORE"
                and {"claude", "codex"} <= set(readback["owner_access_families"])
                and readback["session_read"] is not None)
            if not claims["OWNED_PROBE_OK"]:
                failures.append("OWNED_PROBE_OK")
    except Exception as exc:  # receipt first, then nonzero exit
        claims["error"] = f"{type(exc).__name__}: {exc}"
        failures.append("EXCEPTION")

    claims["claims"] = {
        "PLUGIN_INSTALL_OK": claims.get("PLUGIN_INSTALL_OK", False),
        "MCP_INITIALIZE_OK": claims.get("MCP_INITIALIZE_OK", False),
        "MCP_CONNECTED": claims.get("MCP_CONNECTED", False),
        "TOOLS_LIST_OK": claims.get("TOOLS_LIST_OK", False),
        "TOOL_CALL_OK": claims.get("TOOL_CALL_OK", False),
        "SKILLS_BYTES": claims.get("SKILLS_BYTES", False),
        **({"OWNED_PROBE_OK": claims.get("OWNED_PROBE_OK", False)}
           if owned else {}),
    }
    claims["claim_sources"] = {
        "PLUGIN_INSTALL_OK": "codex plugin marketplace add + plugin add",
        "MCP_INITIALIZE_OK": "app-server initialize frame",
        "MCP_CONNECTED": "app-server mcpServerStatus/list runtimeStatus",
        "TOOLS_LIST_OK": "app-server mcpServerStatus/list tools",
        "tool_count_actual": "app-server mcpServerStatus/list tools",
        "project_create_capability_actual":
            "app-server mcpServerStatus/list tools",
        "TOOL_CALL_OK": "app-server mcpServer/tool/call",
        "SKILLS_BYTES": "installed CODEX_HOME vs shared Skills bytes",
        "OWNED_PROBE_OK": "fleet_owned_probe.py (separate read-only chain)",
        "MODEL_SKILL_BEHAVIOR": "not requested",
    }
    claims["MODEL_SKILL_BEHAVIOR"] = "NOT_RUN"
    claims["failures"] = failures
    (out / "RECEIPT.json").write_text(
        json.dumps(claims, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "RECEIPT.md").write_text(
        "# Codex local client E2E\n\n"
        f"- Evidence level: `{claims.get('evidence_level')}`\n"
        f"- Codex: `{claims.get('codex_version', 'UNKNOWN')}`\n"
        f"- Plugin install: `{claims.get('PLUGIN_INSTALL_OK')}`\n"
        f"- MCP connected: `{claims.get('MCP_CONNECTED')}`\n"
        f"- Tools listed (app-server): `{claims.get('tool_count_actual', 0)}`\n"
        f"- Project-create capability (app-server): "
        f"`{claims.get('project_create_capability_actual')}`\n"
        f"- Tool call (app-server): `{claims.get('TOOL_CALL_OK')}`\n"
        f"- Independent owned probe: `{claims.get('OWNED_PROBE_OK')}` (separate chain)\n"
        f"- Skills byte-identical: `{claims.get('SKILLS_BYTES')}`\n"
        f"- Model turns requested: `{claims.get('model_turns_requested', 0)}`\n"
        f"- Model/Skill behavior: `{claims.get('MODEL_SKILL_BEHAVIOR')}`\n"
        f"- Real owned runtime: `{owned}`\n"
        f"- Global config modified: `{claims.get('global_config_modified')}`\n"
        f"- Credential env names seen/removed: "
        f"`{claims.get('credential_env_names_present')}` "
        f"(values never recorded)\n"
        f"- Login copied: `{claims.get('login_copied')}`\n"
        f"- Failures: `{failures}`\n",
        encoding="utf-8")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
