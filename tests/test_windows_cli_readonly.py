#!/usr/bin/env python3
"""Windows read-only CLI boundary: `status` answers, it does not traceback.

The Python daemon is intentionally POSIX-only; the Windows path is the native
C++ Core (#334), not this CLI. These tests only assert the CLI's *boundary*
behaviour on a platform with no `os.getuid` and a GBK console:

  * no uid, socket path or privileged actor is invented;
  * `status --json` returns a controlled NOT_RUNNING / UNSUPPORTED_PLATFORM;
  * JSON stays ASCII-safe when the console is GBK;
  * help text gets an explicit UTF-8 stdout contract, with tests.

Nothing here starts a daemon, opens a socket, or claims Windows support.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = _load("serveros_runtime_cli_windows_readonly", "cli.py")


def _without_getuid(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delattr(os, "getuid", raising=False)


def test_socket_path_uses_xdg_runtime_dir_when_set(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/tmp/runtime-test")
    assert cli.daemon_socket_path() == Path(
        "/tmp/runtime-test/serveros/serveros.sock")


def test_socket_path_uses_getuid_when_xdg_is_unset(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(os, "getuid", lambda: 1006, raising=False)
    assert cli.daemon_socket_path() == Path(
        "/run/user/1006/serveros/serveros.sock")


def test_socket_path_refuses_without_getuid(monkeypatch):
    _without_getuid(monkeypatch)
    with pytest.raises(cli.PlatformUnsupported) as exc:
        cli.daemon_socket_path()
    message = str(exc.value)
    assert "XDG_RUNTIME_DIR" in message
    assert "os.getuid" in message
    assert "POSIX-only" in message
    # No uid and no fallback socket path are invented.
    assert "/run/user/" not in message


def test_status_json_on_unsupported_platform_is_controlled(monkeypatch, capsys):
    _without_getuid(monkeypatch)
    rc = cli.main(["status", "--json"])
    captured = capsys.readouterr()
    assert rc == cli.EXIT_COULD_NOT_ANSWER == 2
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["runtime"] == "NOT_RUNNING"
    assert payload["platform"] == "UNSUPPORTED_PLATFORM"
    assert "Traceback" not in captured.err
    assert "os.getuid" in payload["detail"]
    # The Windows console is GBK; this JSON must be decodable as ASCII.
    assert all(ord(ch) < 128 for ch in captured.out)


def test_status_human_on_unsupported_platform_exits_2(monkeypatch, capsys):
    _without_getuid(monkeypatch)
    rc = cli.main(["status"])
    captured = capsys.readouterr()
    assert rc == cli.EXIT_COULD_NOT_ANSWER
    assert "POSIX-only" in captured.err
    assert "Traceback" not in captured.err


def test_status_uses_explicit_xdg_runtime_dir_without_getuid(
        monkeypatch, capsys, tmp_path):
    """An explicit path is honoured; the platform boundary is only for the
    case where no correct path exists at all."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delattr(os, "getuid", raising=False)
    rc = cli.main(["status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == cli.EXIT_COULD_NOT_ANSWER
    assert payload["runtime"] == "NOT_RUNNING"
    assert "platform" not in payload
    assert str(tmp_path) in payload["detail"]


def test_emit_json_is_ascii_safe(monkeypatch):
    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buffer)
    cli.emit({"detail": "§ Windows 中文"}, as_json=True)
    output = buffer.getvalue()
    assert all(ord(ch) < 128 for ch in output)
    assert json.loads(output)["detail"] == "§ Windows 中文"


class _FakeStdout:
    def __init__(self, encoding):
        self.encoding = encoding
        self.calls = []

    def reconfigure(self, **kwargs):
        self.calls.append(kwargs)
        self.encoding = kwargs.get("encoding", self.encoding)


def test_portable_stdout_reconfigures_a_gbk_stream(monkeypatch):
    fake = _FakeStdout("gbk")
    monkeypatch.setattr(sys, "stdout", fake)
    cli._portable_stdout()
    assert fake.calls == [{"encoding": "utf-8", "errors": "backslashreplace"}]


def test_portable_stdout_leaves_utf8_alone(monkeypatch):
    fake = _FakeStdout("utf-8")
    monkeypatch.setattr(sys, "stdout", fake)
    cli._portable_stdout()
    assert fake.calls == []


def test_help_bytes_are_utf8_under_a_gbk_console():
    """A GBK console must not make help bytes undecodable as UTF-8.

    `PYTHONIOENCODING=gbk` reproduces the stream the Windows console creates.
    The CLI has to make its own stdout UTF-8 before argparse prints the `§`
    in the help text; this is a per-process stream contract, not a system
    code-page change.
    """
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(RUNTIME)!r}); "
        "import cli; cli.main(['--help'])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        timeout=60,
        cwd=str(REPO),
        env={**os.environ, "PYTHONIOENCODING": "gbk",
             "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    text = result.stdout.decode("utf-8")  # must not raise
    assert "§" in text
