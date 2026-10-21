"""Bounded byte framing for a child process speaking newline-delimited JSON.

`select()` on a text pipe plus `readline()` is two buffers: `select()` watches
the file descriptor, while `TextIOWrapper` may already hold the next line. A
child that writes a notification and its reply in one burst therefore leaves
`select()` reporting "not ready" for a line that is already buffered, and the
reader times out on a response it has actually received.

This reader owns exactly one buffer, reads raw bytes with `os.read`, and hands
whole lines to a queue. Both the size of a line and the total look-ahead are
bounded, so a corrupt or hostile child cannot grow the buffer without limit.
"""
from __future__ import annotations

import os
import queue
import threading
import time

DEFAULT_MAX_LINE_BYTES = 16 * 1024 * 1024
_READ_CHUNK = 64 * 1024
_EOF = object()


class FramingError(RuntimeError):
    """The peer sent more than the bounded line/buffer limit."""


class LineFramer:
    """Read newline-delimited byte frames from `stream` without losing bursts."""

    def __init__(self, stream, *, max_line_bytes: int = DEFAULT_MAX_LINE_BYTES):
        self._fd = stream.fileno()
        self._max = int(max_line_bytes)
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        buffer = bytearray()
        while True:
            try:
                chunk = os.read(self._fd, _READ_CHUNK)
            except OSError as exc:  # closed while reading is EOF, not a crash
                self._queue.put(FramingError(f"read failed: {exc}"))
                return
            if not chunk:
                self._queue.put(_EOF)
                return
            buffer.extend(chunk)
            while True:
                index = buffer.find(b"\n")
                if index < 0:
                    break
                line = bytes(buffer[:index])
                del buffer[: index + 1]
                if len(line) > self._max:
                    self._queue.put(FramingError(
                        f"frame exceeds {self._max} bytes"))
                    return
                self._queue.put(line)
            if len(buffer) > self._max:
                self._queue.put(FramingError(
                    f"unterminated frame exceeds {self._max} bytes"))
                return

    def next_line(self, timeout: float) -> bytes:
        """Return one frame, raising `TimeoutError`, `EOFError` or `FramingError`."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("no frame within the timeout")
            try:
                item = self._queue.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError("no frame within the timeout") from exc
            if item is _EOF:
                raise EOFError("peer closed stdout")
            if isinstance(item, FramingError):
                raise item
            return item
