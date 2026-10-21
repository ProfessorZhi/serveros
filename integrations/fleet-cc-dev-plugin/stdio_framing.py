"""Bounded newline byte framing, including total queued frames and bytes.

A terminal overflow rejects the stream explicitly. Already queued frames are not
silently evicted to make room for an error or a later response.
"""
from __future__ import annotations

import os
import queue
import threading
import time

DEFAULT_MAX_LINE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_QUEUE_FRAMES = 128
DEFAULT_MAX_QUEUE_BYTES = 32 * 1024 * 1024
_READ_CHUNK = 64 * 1024


class FramingError(RuntimeError):
    """The peer exceeded a framing/resource budget or a write failed."""


def write_all_fd(fd: int, data: bytes) -> None:
    """Forward exact bytes, including interrupted and permitted short writes."""
    view = memoryview(data)
    while view:
        try:
            count = os.write(fd, view)
        except InterruptedError:
            continue
        if count <= 0:
            raise FramingError("stdio write made no progress")
        view = view[count:]


class LineFramer:
    def __init__(self, stream, *, max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
                 max_queue_frames: int = DEFAULT_MAX_QUEUE_FRAMES,
                 max_queue_bytes: int = DEFAULT_MAX_QUEUE_BYTES):
        self._fd = stream.fileno()
        self._max = int(max_line_bytes)
        self._max_bytes = int(max_queue_bytes)
        if min(self._max, int(max_queue_frames), self._max_bytes) <= 0:
            raise ValueError("framing budgets must be positive")
        self._queue: queue.Queue = queue.Queue(maxsize=int(max_queue_frames))
        self._queued_bytes = 0
        self._condition = threading.Condition()
        self._failure: FramingError | None = None
        self._eof = False
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _finish(self, error: str | None = None) -> None:
        with self._condition:
            if error is not None:
                self._failure = FramingError(error)
            self._eof = True
            self._condition.notify_all()

    def _enqueue(self, line: bytes) -> bool:
        with self._condition:
            if self._queue.full() or self._queued_bytes + len(line) > self._max_bytes:
                self._failure = FramingError("frame queue exceeds its frames/bytes budget")
                self._eof = True
                self._condition.notify_all()
                return False
            self._queue.put_nowait(line)
            self._queued_bytes += len(line)
            self._condition.notify_all()
            return True

    def _pump(self) -> None:
        buffer = bytearray()
        while True:
            try:
                chunk = os.read(self._fd, _READ_CHUNK)
            except InterruptedError:
                continue
            except OSError as exc:
                self._finish(f"read failed: {exc}")
                return
            if not chunk:
                self._finish("unterminated frame at EOF" if buffer else None)
                return
            buffer.extend(chunk)
            while True:
                index = buffer.find(b"\n")
                if index < 0:
                    break
                if index > self._max:
                    self._finish(f"frame exceeds {self._max} bytes")
                    return
                line = bytes(buffer[:index])
                del buffer[:index + 1]
                if not self._enqueue(line):
                    return
            if len(buffer) > self._max:
                self._finish(f"unterminated frame exceeds {self._max} bytes")
                return

    def next_line(self, timeout: float) -> bytes:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while True:
                if self._failure is not None:
                    raise self._failure
                try:
                    line = self._queue.get_nowait()
                except queue.Empty:
                    if self._eof:
                        raise EOFError("peer closed stdout")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("no frame within the timeout")
                    self._condition.wait(remaining)
                else:
                    self._queued_bytes -= len(line)
                    return line
