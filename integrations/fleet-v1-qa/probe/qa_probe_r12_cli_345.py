#!/usr/bin/env python3
"""Independent probe for #345 Windows read-only CLI boundary.

Asserts: no fabricated uid/socket, controlled UNSUPPORTED_PLATFORM/exit 2,
ASCII-safe JSON, UTF-8 help under a GBK stream, and unchanged Linux paths.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

TREE = Path(os.environ["QA_TREE"]).resolve()
sys.path.insert(0, str(TREE / "runtime"))

FAIL = 0
TOTAL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAIL, TOTAL
    TOTAL += 1
    if cond:
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name} {detail}")


spec = importlib.util.spec_from_file_location("qa_cli_345", TREE / "runtime" / "cli.py")
cli = importlib.util.module_from_spec(spec)
sys.modules["qa_cli_345"] = cli
spec.loader.exec_module(cli)

had_getuid = hasattr(os, "getuid")
saved_getuid = getattr(os, "getuid", None)
saved_xdg = os.environ.pop("XDG_RUNTIME_DIR", None)


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


try:
    if had_getuid:
        del os.getuid

    try:
        cli.daemon_socket_path()
        raised = False
        msg = ""
    except cli.PlatformUnsupported as exc:
        raised = True
        msg = str(exc)
    check("socket/refuses-without-getuid", raised)
    check("socket/no-fabricated-uid", "/run/user/" not in msg, msg)
    check("socket/names-xdg", "XDG_RUNTIME_DIR" in msg, msg)
    check("socket/names-posix-only", "POSIX-only" in msg, msg)

    rc, out, err = run_main(["status", "--json"])
    check("status/json-rc2", rc == 2, f"rc={rc}")
    try:
        payload = json.loads(out)
    except ValueError:
        payload = {}
    check("status/runtime-not-running", payload.get("runtime") == "NOT_RUNNING",
          repr(payload))
    check("status/platform-unsupported",
          payload.get("platform") == "UNSUPPORTED_PLATFORM", repr(payload))
    check("status/ok-false", payload.get("ok") is False, repr(payload))
    check("status/json-ascii", all(ord(ch) < 128 for ch in out))
    check("status/no-traceback", "Traceback" not in err, err)
    check("status/no-fabricated-uid-in-output", "/run/user/" not in out + err)

    rc_h, out_h, err_h = run_main(["status"])
    check("status/human-rc2", rc_h == 2, f"rc={rc_h}")
    check("status/human-no-traceback", "Traceback" not in err_h, err_h)
    check("status/human-names-unsupported-platform",
          "UNSUPPORTED_PLATFORM" in err_h, err_h)
finally:
    if had_getuid:
        os.getuid = saved_getuid
    if saved_xdg is not None:
        os.environ["XDG_RUNTIME_DIR"] = saved_xdg

# Linux paths are unchanged.
os.environ["XDG_RUNTIME_DIR"] = "/tmp/r12-runtime"
check("linux/xdg-path",
      cli.daemon_socket_path() == Path("/tmp/r12-runtime/serveros/serveros.sock"))
if had_getuid:
    os.environ.pop("XDG_RUNTIME_DIR", None)
    saved = os.getuid
    os.getuid = lambda: 1006
    check("linux/getuid-path",
          cli.daemon_socket_path() == Path("/run/user/1006/serveros/serveros.sock"))
    os.getuid = saved

# GBK console: help must be valid UTF-8 and keep the section mark.
code = ("import sys; sys.path.insert(0, %r); import cli; cli.main(['--help'])"
        % str(TREE / "runtime"))
env = {**os.environ, "PYTHONIOENCODING": "gbk", "PYTHONDONTWRITEBYTECODE": "1"}
completed = subprocess.run([sys.executable, "-c", code], cwd=str(TREE),
                           capture_output=True, timeout=60, env=env)
check("gbk/help-rc0", completed.returncode == 0,
      completed.stderr.decode("utf-8", "replace")[:300])
try:
    text = completed.stdout.decode("utf-8")
    decoded = True
except UnicodeDecodeError as exc:
    text = ""
    decoded = False
    check("gbk/help-utf8", False, str(exc))
if decoded:
    check("gbk/help-utf8", True)
    check("gbk/help-section-mark", "§" in text)

buffer = io.StringIO()
with contextlib.redirect_stdout(buffer):
    cli.emit({"detail": "§ Windows 中文"}, as_json=True)
check("json/emit-ascii", all(ord(ch) < 128 for ch in buffer.getvalue()))
check("json/emit-roundtrip",
      json.loads(buffer.getvalue())["detail"] == "§ Windows 中文")

print(f"TOTAL {TOTAL} FAIL {FAIL}")
raise SystemExit(1 if FAIL else 0)
