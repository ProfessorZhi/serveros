"""Fleet Next Agent Monitor — the monitor body (not the activation wrapper).

The wrapper in `integrations/fleet-next-agent-monitor-activation/` (PR #482) asks
for a `--monitor-entry module:callable` and refuses when it cannot resolve one.
This package is the entry it resolves:

    from fleet_next_agent_monitor.runtime import tick   # zero arguments

It reads authorized Legacy facts through an injected read-only client, classifies
them into a vocabulary that has no word for "done", deduplicates against a
durable cursor, and hands at most two kinds of fact to the single #423 notifier.
It never sends, starts, stops, restarts or writes anywhere but its own state
file, and it reports `VERIFIED` only when the far end carried native activity.
"""
from __future__ import annotations

from .contract import (
    DISPATCHABLE,
    NOT_SUCCESS,
    STATUS_TERMINAL_VERIFIED,
    STATUS_UNKNOWN,
    MonitorRefused,
)
from .core import MonitorCore
from .runtime import build_monitor, main, tick
from .sources import Grant, LegacyFactsSource
from .state import DurableState

__all__ = [
    "DISPATCHABLE", "NOT_SUCCESS", "STATUS_TERMINAL_VERIFIED", "STATUS_UNKNOWN",
    "DurableState", "Grant", "LegacyFactsSource", "MonitorCore", "MonitorRefused",
    "build_monitor", "main", "tick",
]
