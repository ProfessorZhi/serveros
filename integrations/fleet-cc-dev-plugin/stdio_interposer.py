#!/usr/bin/env python3
"""Transparent, bounded stdio observation; every reply is from the child.

Valid wire bytes are forwarded exactly. Resource/observation failure is explicit:
metadata records the refusal and the wrapper terminates the child and exits2.
No payload body or credential is logged, and no queued frame is silently evicted.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from stdio_framing import DEFAULT_MAX_LINE_BYTES, FramingError, write_all_fd


def _id_key(value):
    if type(value) not in (str, int):
        return None
    if len(str(value).encode("utf-8")) > 512:
        raise FramingError("request id exceeds metadata budget")
    return type(value).__name__, value


def _metadata(direction: str, event: dict) -> dict:
    method, params, result = event.get("method"), event.get("params"), event.get("result")
    if method is not None and not isinstance(method, str):
        raise FramingError("invalid RPC method type")
    if event.get("id") is not None and type(event["id"]) not in (str, int):
        raise FramingError("invalid RPC request id type")
    for value in (method, event.get("id"), (params or {}).get("name") if isinstance(params, dict) else None):
        if isinstance(value, str) and len(value.encode("utf-8")) > 512:
            raise FramingError("metadata field exceeds budget")
    row = {"direction": direction, "method": method, "id": event.get("id"),
           "sha256": hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    if isinstance(params, dict) and method == "tools/call" and isinstance(params.get("name"), str):
        row["tool"] = params["name"]
    if isinstance(result, dict):
        if isinstance(result.get("tools"), list):
            row["tool_count"] = len(result["tools"])
        if "isError" in result:
            row["is_error"] = bool(result["isError"])
    if method is None:
        row["response_ok"] = event.get("jsonrpc") == "2.0" and isinstance(result, dict) and "error" not in event
    if "error" in event:
        row["error"] = "present"
        error = event["error"]
        if isinstance(error, dict) and type(error.get("code")) in (str, int):
            if len(str(error["code"]).encode()) > 64:
                raise FramingError("error code exceeds metadata budget")
            row["error_code"] = error["code"]
    return row


class Observation:
    def __init__(self, log: Path, *, max_line_bytes=DEFAULT_MAX_LINE_BYTES,
                 max_frames=4096, max_wire_bytes=32 * 1024 * 1024,
                 max_pending=256, max_metadata_bytes=4 * 1024 * 1024):
        self.max_line_bytes = int(max_line_bytes)
        self.max_frames, self.max_wire_bytes = int(max_frames), int(max_wire_bytes)
        self.max_pending, self.max_metadata_bytes = int(max_pending), int(max_metadata_bytes)
        if min(self.max_line_bytes, self.max_frames, self.max_wire_bytes, self.max_pending,
               self.max_metadata_bytes - 512) <= 0:
            raise ValueError("observation budgets must be positive")
        self.connection_id = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.failed = threading.Event()
        self.pending = {}
        self.frames = self.wire_bytes = self.buffer_peak = 0
        log.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        info = os.fstat(self.fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            self.close()
            raise FramingError("observation log is not an owned regular file")

    def close(self):
        if getattr(self, "fd", None) is not None:
            os.close(self.fd)
            self.fd = None

    def __del__(self):
        self.close()

    def _append(self, row, *, failure=False):
        data = (json.dumps({"connection_id": self.connection_id, **row}, ensure_ascii=False) + "\n").encode()
        reserve = 0 if failure else 512
        if os.fstat(self.fd).st_size + len(data) + reserve > self.max_metadata_bytes:
            raise FramingError("observation metadata budget exceeded")
        write_all_fd(self.fd, data)

    def fail(self, reason):
        with self.lock:
            if self.failed.is_set():
                return
            self.failed.set()
            try:
                self._append({"event": "observation_failed", "reason": str(reason)[:200]}, failure=True)
            except (OSError, FramingError):
                pass
            try:
                write_all_fd(sys.stderr.fileno(), b"FLEET_OBSERVATION_FAILED\n")
            except OSError:
                pass

    def chunk(self, count):
        with self.lock:
            self.wire_bytes += count
            if self.wire_bytes > self.max_wire_bytes:
                raise FramingError("observation wire byte budget exceeded")

    def line(self, direction, line):
        with self.lock:
            self.frames += 1
            if self.frames > self.max_frames:
                raise FramingError("observation frame budget exceeded")
            if not line.strip():
                return
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return
            if not isinstance(event, dict):
                return
            row = _metadata(direction, event)
            key = _id_key(event.get("id"))
            method = event.get("method")
            if direction == "cc->owned" and isinstance(method, str) and key is not None:
                if key in self.pending or len(self.pending) >= self.max_pending:
                    raise FramingError("observation pending request budget or duplicate id")
                self.pending[key] = method
            elif direction == "owned->cc" and method is None and key in self.pending:
                row["request_method"] = self.pending.pop(key)
            self._append(row)


def _pump(source, sink, observation: Observation, direction: str) -> None:
    buffer = bytearray()
    try:
        while not observation.failed.is_set():
            try:
                chunk = os.read(source.fileno(), 65536)
            except InterruptedError:
                continue
            if not chunk:
                if buffer:
                    raise FramingError("unterminated observed frame at EOF")
                break
            observation.chunk(len(chunk))
            buffer.extend(chunk)
            observation.buffer_peak = max(observation.buffer_peak, len(buffer))
            while True:
                index = buffer.find(b"\n")
                if index < 0:
                    break
                if index > observation.max_line_bytes:
                    raise FramingError("observed frame exceeds line byte budget")
                line = bytes(buffer[:index])
                del buffer[:index + 1]
                observation.line(direction, line)
            if len(buffer) > observation.max_line_bytes:
                raise FramingError("unterminated observed frame exceeds line byte budget")
            # Record the request before the child can answer; record its result
            # before CC can send initialized/another request. Wire stays exact.
            write_all_fd(sink.fileno(), chunk)
    except Exception as exc:
        observation.fail(str(exc) if isinstance(exc, FramingError) else type(exc).__name__)
    finally:
        if direction == "cc->owned":
            try:
                sink.close()
            except OSError:
                pass


def _copy_stderr(source, observation):
    try:
        while True:
            chunk = os.read(source.fileno(), 65536)
            if not chunk:
                return
            write_all_fd(sys.stderr.fileno(), chunk)
    except (OSError, FramingError) as exc:
        observation.fail(type(exc).__name__)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        return 2
    split = argv.index("--")
    head, command = argv[:split], argv[split + 1:]
    if len(head) != 2 or head[0] != "--log" or not command:
        return 2
    try:
        observation = Observation(Path(head[1]))
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    except (OSError, FramingError, ValueError) as exc:
        print(f"FLEET_OBSERVATION_FAILED: {type(exc).__name__}", file=sys.stderr)
        return 2
    upstream = threading.Thread(target=_pump, args=(sys.stdin.buffer, child.stdin, observation, "cc->owned"), daemon=True)
    downstream = threading.Thread(target=_pump, args=(child.stdout, sys.stdout.buffer, observation, "owned->cc"), daemon=True)
    errors = threading.Thread(target=_copy_stderr, args=(child.stderr, observation), daemon=True)
    for thread in (upstream, downstream, errors):
        thread.start()
    while child.poll() is None and not observation.failed.wait(0.02):
        pass
    if observation.failed.is_set() and child.poll() is None:
        child.terminate()
    try:
        rc = child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        child.kill()
        rc = child.wait(timeout=2)
    downstream.join(timeout=2)
    errors.join(timeout=2)
    if downstream.is_alive() or errors.is_alive():
        observation.fail("stdio drain exceeded its deadline")
    # An upstream read may await a still-open parent stdin after child exit.
    # It is a daemon; do not wait forever or reuse its descriptors before exit.
    if not upstream.is_alive() and not downstream.is_alive() and not errors.is_alive():
        observation.close()
    return 2 if observation.failed.is_set() else rc


if __name__ == "__main__":
    raise SystemExit(main())
