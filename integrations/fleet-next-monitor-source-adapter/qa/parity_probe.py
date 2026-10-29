#!/usr/bin/env python3
"""The two-path probe: one legacy answer, two processes, one journal.

This file is **not** part of the adapter. `fleet_next_monitor_source_adapter/`
never imports it, and it lives outside that package so an `import
fleet_next_monitor_source_adapter` cannot pull the monitor in — the dependency
stays one-way (adapter ← test harness), which is what keeps a native→adapter
import cycle impossible.

It exists so that a *test* can run the monitor's own path and this adapter's
path as **separate Python processes** over one shared journal / ledger / state,
then read the journal back: "sent once" and "sent nothing" become facts about
the filesystem instead of facts about one interpreter's objects.

Two entry points:

*   ``parity_probe:notifier`` — the ``--notifier-entry`` factory #493's CLI
    resolves. It is #423's real ``MilestoneNotifier`` (sqlite claim/receipt
    ledger, transport dedupe) over a **journal provider**, which is the only
    fake here: the far end of the notification. The journal is written with
    ``O_APPEND`` + ``fsync`` so concurrent appends stay one line each.
*   ``python qa/parity_probe.py compat <scenario-dir>`` — one compat-path tick:
    the real #499 ``FixtureLegacyClient`` + adapter + ``CompatMonitorSource``,
    then the *same* #493 ``MonitorCore``, the *same* state file and the *same*
    notifier as the native path.

Environment: ``W19_JOURNAL`` (journal path), ``W19_LEDGER`` (sqlite ledger
path), ``W19_CONVERSATION`` (optional conversation ref).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # …/fleet-next-monitor-source-adapter/qa
PACKAGE_ROOT = HERE.parent                      # …/fleet-next-monitor-source-adapter
REPO = PACKAGE_ROOT.parent.parent               # the repository root

#: Named, not globbed: two integration directories on one `sys.path` can shadow
#: each other's top-level modules, and a probe that resolves a different module
#: than the tree it claims to measure is worse than no probe.
_COMPANIONS = ("fleet-next-agent-monitor", "fleet-v1-milestone-notify")

for _path in [PACKAGE_ROOT, *(REPO / "integrations" / name for name in _COMPANIONS)]:
    if _path.is_dir() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


# -- the only fake: the far end of the notification -------------------------

def _append(path: Path, record: dict) -> None:
    """One line, appended and fsynced, so a reader sees every completed send."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(handle, "a", encoding="utf-8") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
               if line.strip())


class JournalProvider:
    """A provider whose memory is the journal the test reads back."""

    provider_id = "provider:w19-parity-journal"
    transport = "fake"

    def __init__(self, journal: str | Path) -> None:
        self.journal = Path(journal)
        self.calls: list[dict] = []

    def capabilities(self) -> dict:
        return {"provider": self.provider_id, "transport": self.transport,
                "capabilities": {"human.notification.transport": {"state": "SUPPORTED"}}}

    def notify(self, *, conversation_ref: str, text: str,
               idempotency_key: str) -> dict:
        record = {"conversation_ref": conversation_ref, "text": text,
                  "idempotency_key": idempotency_key,
                  "remote_id": f"om_w19_{_line_count(self.journal) + 1}"}
        _append(self.journal, record)
        self.calls.append(record)
        return {"delivered": True, "remote_id": record["remote_id"]}


def notifier(config=None):
    """``module:callable`` for #493's ``--notifier-entry``: #423's real notifier."""
    import milestone_notify

    journal = os.environ.get("W19_JOURNAL")
    ledger = os.environ.get("W19_LEDGER")
    if not journal or not ledger:
        raise RuntimeError("W19_JOURNAL and W19_LEDGER must name the shared paths")
    return milestone_notify.MilestoneNotifier(
        JournalProvider(journal), ledger,
        conversation_ref=os.environ.get("W19_CONVERSATION", "chat:w19-parity"))


# -- the compat path, as its own process ------------------------------------

def compat_tick(scenario: str | Path) -> dict:
    """One tick through the adapter, the real core and the real notifier."""
    from fleet_next_agent_monitor.core import MonitorCore
    from fleet_next_agent_monitor.state import DurableState
    from fleet_next_monitor_source_adapter import (
        CallerGrant,
        CompatMonitorSource,
        FixtureLegacyClient,
        LegacyReadOnlySourceAdapter,
    )

    directory = Path(scenario).resolve()
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    fixture = json.loads((directory / "fixture.json").read_text(encoding="utf-8"))

    declared = config["grant"]
    grant = CallerGrant.from_mapping({
        "principal": declared["principal"],
        "devices": list(declared["devices"]),
        "projects": list(declared.get("projects") or []),
        "sessions": list(declared["sessions"]),
        "views": list(declared["views"]),
    })
    # The client's declared identity is part of "the same facts": the native
    # path reads it from the same three keys of its own document, so a scenario
    # that moved one of them would move both paths together.
    client = FixtureLegacyClient(
        fixture,
        fleet_instance_id=fixture["fleet_instance_id"],
        route_id=fixture["route_id"],
        boot_id=fixture["boot_id"],
    )
    adapter = LegacyReadOnlySourceAdapter(client, grant, max_readings=500)
    bounds = config.get("bounds") or {}
    # The same bounds the native path reads from the same document, so a tick
    # that differed because of a bound would be visible as a difference.
    core = MonitorCore(
        source=CompatMonitorSource(adapter),
        notifier=notifier(config),
        state=DurableState(directory / "state.json"),
        watches=config["watches"],
        debounce_ticks=int(bounds.get("debounce_ticks", 2)),
        stale_after=float(bounds.get("stale_after_seconds", 3600)),
        max_warnings=int(bounds.get("max_warnings", 6)),
    )
    tick = core.run_once()
    tick["client_reads"] = len(client.calls)
    tick["path"] = "compat"
    return tick


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2 or arguments[0] != "compat":
        sys.stderr.write("usage: parity_probe.py compat <scenario-dir>\n")
        return 2
    tick = compat_tick(arguments[1])
    json.dump(tick, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
