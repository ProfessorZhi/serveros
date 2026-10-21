"""Bounded inventory IO: deadlines release callers, not occupied worker slots."""
from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

from .errors import PROVIDER_TIMEOUT, FleetRefusal


def discovery_settings(timeout, workers):
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= 60):
        raise ValueError("discovery_timeout_seconds must be finite and in (0, 60]")
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 16:
        raise ValueError("discovery_workers must be an integer in [1, 16]")
    return float(timeout), workers


class DiscoveryPool:
    def __init__(self, workers=4):
        _, self.workers = discovery_settings(5.0, workers)
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fleet-inventory")
        self._lock = threading.RLock()
        self._pending = {}

    def _finished(self, token, future):
        with self._lock:
            if self._pending.get(token) is future:
                self._pending.pop(token)

    def collect(self, jobs, *, timeout):
        """jobs maps a result label to (exact in-flight query identity, callable).

        There is no completed-result cache and no executor-context exit wait.
        Only a worker completing its real IO releases its capacity.
        """
        timeout, _ = discovery_settings(timeout, self.workers)
        deadline = time.monotonic() + timeout
        selected, results = {}, {}
        for name, (token, call) in jobs.items():
            with self._lock:
                # A callback can be waiting for this lock after IO completed.
                for old, future in list(self._pending.items()):
                    if future.done():
                        self._pending.pop(old, None)
                future = self._pending.get(token)
                if future is None:
                    if len(self._pending) >= self.workers:
                        results[name] = FleetRefusal(
                            PROVIDER_TIMEOUT,
                            "inventory query not started: bounded discovery capacity is occupied")
                        continue
                    future = self._executor.submit(call)
                    self._pending[token] = future
                    # RLock also handles a future already complete here.
                    future.add_done_callback(
                        lambda done, token=token: self._finished(token, done))
                selected[name] = future
        if selected:
            wait(set(selected.values()), timeout=max(0.0, deadline - time.monotonic()))
        for name, future in selected.items():
            if not future.done():
                results[name] = FleetRefusal(
                    PROVIDER_TIMEOUT,
                    "inventory deadline reached; provider may still be responding, sessions remain unknown")
                continue
            try:
                results[name] = future.result()
            except Exception as exc:
                results[name] = exc
        return results

    def close(self, *, wait_for_io=False):
        self._executor.shutdown(wait=wait_for_io, cancel_futures=False)
