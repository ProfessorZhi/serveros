#!/usr/bin/env python3
"""Transparent stdio interposer with a metadata-only wire log.

Usage:
    stdio_interposer.py --log PATH -- COMMAND [ARGS...]

Bytes flow unchanged in both directions. The interposer only observes complete
newline-delimited JSON messages and appends metadata: direction, method, id,
tool name, tool count, error presence/code and a SHA-256. It never stores a raw
request/response body, transcript content or a credential. It does not answer
MCP requests itself; every reply comes from the child process.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path


def _metadata(direction: str, event: dict) -> dict:
    method = event.get("method")
    params = event.get("params")
    result = event.get("result")
    error = event.get("error")
    row = {
        "direction": direction,
        "method": method,
        "id": event.get("id"),
        "sha256": hashlib.sha256(
            json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    if isinstance(params, dict) and method == "tools/call":
        name = params.get("name")
        if isinstance(name, str):
            row["tool"] = name
    if isinstance(result, dict):
        tools = result.get("tools")
        if isinstance(tools, list):
            row["tool_count"] = len(tools)
        if "isError" in result:
            row["is_error"] = bool(result["isError"])
    if error is not None:
        row["error"] = "present"
        if isinstance(error, dict) and isinstance(error.get("code"), (str, int)):
            row["error_code"] = error["code"]
    return row


def _pump(source, sink, log, lock, pending, direction: str) -> None:
    buffer = b""
    while True:
        chunk = os.read(source.fileno(), 65536)
        if not chunk:
            break
        os.write(sink.fileno(), chunk)
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            with lock:
                row = _metadata(direction, event)
                if direction == "cc->owned" and event.get("method"):
                    pending[event.get("id")] = event.get("method")
                elif direction == "owned->cc" and event.get("id") in pending:
                    row["request_method"] = pending.pop(event.get("id"))
                with log.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    if direction == "cc->owned":
        try:
            sink.close()
        except OSError:
            pass
    else:
        try:
            os.close(sink.fileno())
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        print("usage: stdio_interposer.py --log PATH -- COMMAND [ARGS...]",
              file=sys.stderr)
        return 2
    split = argv.index("--")
    head, command = argv[:split], argv[split + 1:]
    if len(head) != 2 or head[0] != "--log" or not command:
        print("usage: stdio_interposer.py --log PATH -- COMMAND [ARGS...]",
              file=sys.stderr)
        return 2
    log_path = Path(head[1])
    if log_path.exists():
        log_path.unlink()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env)
    lock = threading.Lock()
    pending: dict[object, str] = {}
    upstream = threading.Thread(
        target=_pump, args=(sys.stdin.buffer, child.stdin, log_path, lock,
                            pending, "cc->owned"), daemon=True)
    downstream = threading.Thread(
        target=_pump, args=(child.stdout, sys.stdout.buffer, log_path, lock,
                            pending, "owned->cc"), daemon=True)
    error_thread = threading.Thread(
        target=lambda: _copy_stderr(child.stderr), daemon=True)
    upstream.start()
    downstream.start()
    error_thread.start()
    upstream.join()
    downstream.join()
    rc = child.wait()
    error_thread.join(timeout=2)
    return rc


def _copy_stderr(source) -> None:
    while True:
        chunk = os.read(source.fileno(), 65536)
        if not chunk:
            return
        os.write(sys.stderr.fileno(), chunk)


if __name__ == "__main__":
    raise SystemExit(main())
