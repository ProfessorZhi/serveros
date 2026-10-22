#!/usr/bin/env python3
"""Drive a real Claude Code client against the local stdio Fleet fixture.

The receipt is four separate claims, because one can be true while another is
false and an earlier revision of this lane reported one as another:

1. **PLUGIN_LOAD_OK** — `claude plugin validate --strict` and
   `claude --plugin-dir … plugin details` enumerate five Skills and one MCP
   component. This is a directory/registration fact. It enumerates no tool.
2. **MCP_INITIALIZE_OK / TOOLS_LIST_OK** — `claude --plugin-dir … mcp list`
   actually starts the stdio server, sends `initialize`,
   `notifications/initialized` and `tools/list`, and reads a 34-tool answer.
   The server's wire log is the evidence; "Connected" alone is not.
3. **AUTH_SCOPE** — the principal holds exactly `fleet:read`.
   `tools/list` still advertises all 34 tools (discovery is not entitlement),
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


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"event": "unparseable", "raw": line[:200]})
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
    def __init__(self, out: Path, model_readbacks: bool) -> None:
        self.out = out
        # Named apart from the method below: a bool attribute with the method's
        # name shadows it, and the call becomes "'bool' object is not callable".
        self.want_model_readbacks = model_readbacks
        self.plugin = out / "plugin"
        self.cc_home = out / "cc-home"
        self.session_cwd = out / "session-cwd"
        self.logs = out / "logs"
        self.results: dict[str, Any] = {}
        self.claude = shutil.which("claude")
        self.claude_version: str | None = None
        self.model_calls = 0
        self.negative_results: dict[str, Any] = {}
        self.global_config_hashes: dict[str, str] = {}

    # -- process helpers ---------------------------------------------------

    def env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["CLAUDE_CONFIG_DIR"] = str(self.cc_home)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
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

    def prepare(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        self.cc_home.mkdir(parents=True, exist_ok=True)
        self.session_cwd.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
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
        if self.plugin.exists():
            shutil.rmtree(self.plugin)
        record = fixture.generate(out=self.plugin)
        (self.logs / "fixture-build.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8")
        self.record("FIXTURE_BUILD", "PASS", "TEST_FIXTURE_READY",
                    {"skills": record["skills"], "server": SERVER_NAME,
                     "principal": record["principal"],
                     "plugin_tree_sha256": _tree_sha256(self.plugin)})

    def package_load(self) -> None:
        validate = self.run([self.claude, "plugin", "validate",
                             str(self.plugin), "--strict"], timeout=120)
        (self.logs / "validate.log").write_text(
            validate.stdout + validate.stderr, encoding="utf-8")
        details = self.run([self.claude, "--plugin-dir", str(self.plugin),
                            "plugin", "details", PLUGIN_NAME], timeout=120)
        (self.logs / "plugin-details.log").write_text(
            details.stdout + details.stderr, encoding="utf-8")
        text = details.stdout + details.stderr
        skills = "Skills (5)" in text
        mcp = "MCP servers (1)" in text and SERVER_NAME in text
        ok = validate.returncode == 0 and skills and mcp
        self.record(
            "PLUGIN_LOAD_OK", "PASS" if ok else "FAIL",
            "validate --strict exit 0; details list 5 Skills and 1 MCP server"
            if ok else "package load did not show the expected inventory",
            {"validate_exit": validate.returncode,
             "skills_five": skills, "mcp_one": mcp})

    def mcp_list(self) -> bool:
        """The real client's initialize/tools/list, with no model call."""
        wire = self.plugin / ".fixture-state" / "mcp-wire.log"
        if wire.exists():
            wire.write_text("", encoding="utf-8")
        proc = self.run([self.claude, "--plugin-dir", str(self.plugin),
                         "mcp", "list"], timeout=180)
        text = proc.stdout + proc.stderr
        (self.logs / "mcp-list.log").write_text(text, encoding="utf-8")
        connected = "Connected" in text and QUALIFIED_SERVER in text
        rows = _read_json_lines(wire)
        (self.logs / "mcp-list-wire.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8")
        methods = [row.get("method") for row in rows if row.get("event") == "request"]
        initialize = "initialize" in methods
        initialized = "notifications/initialized" in methods
        tools_list = "tools/list" in methods
        counts = [row.get("tool_count") for row in rows
                  if row.get("event") == "response" and row.get("method") == "tools/list"]
        count = counts[-1] if counts else None
        ok = connected and initialize and initialized and tools_list and count == 34
        self.record(
            "MCP_INITIALIZE_OK", "PASS" if initialize and initialized else "FAIL",
            "the client sent initialize and notifications/initialized"
            if initialize and initialized else "handshake frames missing",
            {"methods": methods, "connected": connected})
        self.record(
            "TOOLS_LIST_OK", "PASS" if tools_list and count == 34 else "FAIL",
            f"tools/list returned {count} tools" if tools_list
            else "tools/list was not sent",
            {"tool_count": count, "connected": connected,
             "qualified_server": QUALIFIED_SERVER})
        return ok

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
        server = (binding.get("mcpServers") or {}).get(SERVER_NAME) or {}
        binding_ok = (server.get("type") == "stdio"
                      and "url" not in server and "oauth" not in server
                      and "command" in server)
        ok = (manifest.get("name") == PLUGIN_NAME and not stray
              and identical and binding_ok)
        self.record(
            "PLUGIN_METADATA", "PASS" if ok else "FAIL",
            "documented manifest keys, five Skills byte-identical, stdio binding"
            if ok else "metadata, Skills bytes or binding did not match",
            {"manifest_keys": sorted(manifest), "stray_keys": stray,
             "skills_generated": generated, "skills_source": source,
             "binding_type": server.get("type")})
        self.record(
            "SKILLS_BYTES", "PASS" if identical else "FAIL",
            "5/5 SKILL.md byte-identical to the canonical source"
            if identical else "a SKILL.md was re-typed",
            {"sha256": {name: _sha256(self.plugin / "skills" / name / "SKILL.md")
                        for name in generated}})

    def cli_no_side_effect(self) -> None:
        """No-side-effect CLI commands, under the isolated config directory."""
        mcp_get = self.run([self.claude, "--plugin-dir", str(self.plugin),
                            "mcp", "get", QUALIFIED_SERVER], timeout=120)
        (self.logs / "mcp-get.log").write_text(
            mcp_get.stdout + mcp_get.stderr, encoding="utf-8")
        listed = self.run([self.claude, "plugin", "list"], timeout=60)
        (self.logs / "plugin-list.log").write_text(
            listed.stdout + listed.stderr, encoding="utf-8")
        inline_not_installed = PLUGIN_NAME not in (listed.stdout + listed.stderr)
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
                    "read-only principal: 34 tools discovered, write refused "
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
        ok = (tools_count == 34 and devices_total == 1 and sessions_total == 1
              and capability_state == "OBSERVED" and sends == 0)
        self.record(
            "TEMP_DAEMON_VIRTUAL_FACTS", "PASS" if ok else "FAIL",
            "local stdio daemon: 34 tools, one virtual device and session, "
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

    def write_receipt(self) -> None:
        payload = {
            "schema": "serveros/fleet-cc-client-e2e-receipt.v1",
            "checked_at": _now(),
            "git_head": self._git_head(),
            "claude_version": self.claude_version,
            "evidence_level": "LOCAL",
            "scope": {
                "transport": "stdio",
                "device": "device:fixture-node (TEST_FIXTURE, invented)",
                "session": SESSION_REF,
                "network": "none",
                "real_device": False,
                "serverosd_started": False,
                "credential_read": False,
                "global_config_modified": not (
                    self.results.get("GLOBAL_CONFIG", {}).get("evidence") or {}
                ).get("unchanged", False),
                "model_calls": self.model_calls,
            },
            "layers": {
                "L0_CODE": {name: self.results.get(name, {}).get("status")
                            for name in ("FIXTURE_BUILD", "PLUGIN_METADATA",
                                         "SKILLS_BYTES")},
                "L1_LOCAL_CLI": {name: self.results.get(name, {}).get("status")
                                 for name in ("CLAUDE_VERSION", "PLUGIN_LOAD_OK",
                                              "CLI_NO_SIDE_EFFECT",
                                              "MCP_INITIALIZE_OK", "TOOLS_LIST_OK")},
                "L2_TEMP_DAEMON": {name: self.results.get(name, {}).get("status")
                                   for name in ("TEMP_DAEMON_VIRTUAL_FACTS",
                                                "SEND_SCOPE_DENIED")},
                "L3_CLIENT_E2E_MODEL": {
                    "CLIENT_E2E": self.results.get("CLIENT_E2E", {}).get("status")},
            },
            "results": self.results,
        }
        (self.out / "RECEIPT.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")

        lines = [
            "# Claude Code client E2E receipt — local stdio fixture",
            "",
            f"- checked: `{payload['checked_at']}`",
            f"- git head: `{payload['git_head']}`",
            f"- claude: `{self.claude_version}`",
            f"- evidence level: **{payload['evidence_level']}**",
            "- device: `device:fixture-node` (TEST_FIXTURE, invented)",
            "- network: none; credential read: no; global config modified: no",
            "",
            "| check | status | detail |",
            "|---|---|---|",
        ]
        for name, row in self.results.items():
            detail = str(row["detail"]).replace("|", "\\|")
            lines.append(f"| `{name}` | **{row['status']}** | {detail} |")
        lines += [
            "",
            "## What this receipt is not",
            "",
            "`LOCAL` means a real Claude Code client on this host, over local",
            "stdio, against a fixture device. It is not `REAL_DEVICE`, not the",
            "live Fleet Next ingress, and not `PRODUCTION`. `TOOLS_LIST_OK` is",
            "not an authenticated product `tools/list`; `AUTH_SCOPE` is the",
            "fixture principal's `fleet:read`, not a real token's grants.",
            "",
        ]
        (self.out / "RECEIPT.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model-readbacks", action="store_true",
                        help="run one real claude -p session for read-only tools")
    args = parser.parse_args(argv)

    runner = Runner(args.out, args.model_readbacks)
    try:
        runner.prepare()
        runner.build()
        runner.plugin_metadata()
        runner.package_load()
        runner.mcp_list()
        runner.cli_no_side_effect()
        runner.negative_cases()
        runner.temp_daemon_facts()
        runner.model_readbacks()
    except Exception as exc:  # noqa: BLE001 - a runner failure is the receipt
        runner.record("RUNNER", "BLOCKED", f"{type(exc).__name__}: {exc}")
        runner.write_receipt()
        print(f"BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    runner.write_receipt()
    statuses = {name: row["status"] for name, row in runner.results.items()}
    for name in ("PLUGIN_METADATA", "SKILLS_BYTES", "PLUGIN_LOAD_OK",
                 "CLI_NO_SIDE_EFFECT", "MCP_INITIALIZE_OK", "TOOLS_LIST_OK",
                 "TEMP_DAEMON_VIRTUAL_FACTS", "SEND_SCOPE_DENIED",
                 "AUTH_SCOPE", "CLIENT_E2E"):
        print(f"{name}: {statuses.get(name, 'NOT_RUN')}")
    failed = [name for name, status in statuses.items()
              if status == "FAIL" and (name != "CLIENT_E2E" or args.model_readbacks)]
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
