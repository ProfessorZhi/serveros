"""M4 — resource levels from each device's Core-Lite reading, and node events.

Pure classification (`device_view`) over the `serveros/fleet-core-lite-readback.v1`
summary FleetService already produces, plus one small stateful step (`sweep`)
that turns level CHANGES into inbox events.

Thresholds are not invented here. They are read from the node's own
`runtime/attribution-contract.yaml` `thresholds` block — the same numbers the
node-local alerts use — so a disk is "85% → 注意, 92% → 报警" on the hub card for
the same reason it is on the node. Each threshold has a recovery margin there
too; a reading has to fall below `threshold - margin` to leave a level, so a disk
sitting at 85.1% does not flap between 注意 and 正常 every half hour.

| reading                  | 注意 (WARN)                   | 报警 (ALERT)                     |
|--------------------------|-------------------------------|----------------------------------|
| filesystem used %        | filesystem_percent_high       | filesystem_percent_critical      |
| memory used %            | memory_percent_high           | —                                |
| swap used %              | swap_percent_high (watermark) | —                                |
| PSI memory avg60         | some ≥ psi…some…_high         | full ≥ psi…full…_high            |
| a watched service        | —                             | FAILED                           |
| the device               | —                             | not connected (was seen before)  |
| GPU memory               | no threshold declared: value only, never a level |

A reading that is UNAVAILABLE or stale is UNCONFIRMED, never OK and never an
alarm. GPU memory has no declared threshold, so it is shown and not judged.
"""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .levels import ALERT, OK, UNCONFIRMED, WARN, WORD, private_file, worst

GiB = 1024 ** 3

#: The keys this module reads from the contract, and nothing else.
THRESHOLD_KEYS = (
    "filesystem_percent_high", "filesystem_percent_critical",
    "filesystem_percent_recovery_margin",
    "memory_percent_high", "memory_percent_recovery_margin",
    "swap_percent_high", "swap_percent_recovery_margin",
    "psi_memory_some_avg60_percent_high", "psi_memory_full_avg60_percent_high",
    "psi_memory_some_avg60_percent_recovery_margin",
)


def load_thresholds(contract_path: str | Path) -> dict[str, float]:
    import yaml
    document = yaml.safe_load(Path(contract_path).read_text(encoding="utf-8")) or {}
    table = document.get("thresholds") or {}
    missing = [key for key in THRESHOLD_KEYS if not isinstance(table.get(key), (int, float))]
    if missing:
        raise ValueError(f"attribution contract lacks thresholds: {missing}")
    return {key: float(table[key]) for key in THRESHOLD_KEYS}


def _level(value: float | None, *, warn: float | None, alert: float | None,
            margin: float, previous: str | None) -> str:
    """The level of one number, with hysteresis on the way down."""
    if value is None:
        return UNCONFIRMED
    # Each bar is checked with its own hysteresis before the bar below it: an
    # ALERT holds until the reading is `margin` under the alert line, even
    # though it is already over the warn line.
    if alert is not None and (value >= alert
                              or (previous == ALERT and value >= alert - margin)):
        return ALERT
    if warn is not None and (value >= warn
                             or (previous in (WARN, ALERT) and value >= warn - margin)):
        return WARN
    return OK


def _present(summary: dict, name: str) -> dict | None:
    observation = (summary.get("observations") or {}).get(name) or {}
    return observation.get("value") if observation.get("state") == "PRESENT" else None


def _psi_avg60(line: Any) -> float | None:
    if isinstance(line, dict):
        value = line.get("avg60")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def device_view(device_ref: str, display_name: str, summary: dict | None,
                thresholds: dict[str, float], previous: dict[str, str] | None = None,
                *, seen_before: bool = True) -> dict[str, Any]:
    """One device's readings for the card, each with its level. PURE."""
    previous = previous or {}
    readings: list[dict[str, Any]] = []

    def add(key, label, text, level, *, value=None):
        readings.append({"key": key, "label": label, "text": text, "level": level,
                         "value": value})

    connected = bool(summary) and summary.get("connected") == "CONNECTED"
    fresh = bool(summary) and (summary.get("freshness") or {}).get("state") == "FRESH"
    if not connected:
        level = ALERT if seen_before and previous.get("reach") in (OK, WARN, ALERT) else UNCONFIRMED
        why = "失联" if level == ALERT else "未接入资源读数"
        add("reach", "连接", why, level)
        return {"device_ref": device_ref, "name": display_name, "connected": False,
                "fresh": False, "readings": readings,
                "level": level, "observed_at": None}
    add("reach", "连接", "已连接" if fresh else "读数已过期", OK if fresh else UNCONFIRMED)

    t = thresholds
    cpu = _present(summary, "cpu")
    if cpu and isinstance(cpu.get("used_percent"), (int, float)):
        add("cpu", "CPU", f"{cpu['used_percent']:.0f}%", OK, value=cpu["used_percent"])
    else:
        add("cpu", "CPU", WORD[UNCONFIRMED], UNCONFIRMED)

    memory = _present(summary, "memory")
    if memory and isinstance(memory.get("used_percent"), (int, float)):
        pct = float(memory["used_percent"])
        total = memory.get("total_bytes")
        detail = f"{pct:.0f}%" + (f" / {total / GiB:.0f} GiB" if isinstance(total, (int, float)) else "")
        add("memory", "内存", detail,
            _level(pct, warn=t["memory_percent_high"], alert=None,
                   margin=t["memory_percent_recovery_margin"], previous=previous.get("memory")),
            value=pct)
    else:
        add("memory", "内存", WORD[UNCONFIRMED], UNCONFIRMED)

    swap = _present(summary, "swap")
    if swap and isinstance(swap.get("total_bytes"), (int, float)) and swap["total_bytes"] > 0:
        used = swap.get("used_bytes")
        if not isinstance(used, (int, float)) and isinstance(swap.get("free_bytes"), (int, float)):
            used = swap["total_bytes"] - swap["free_bytes"]
        pct = 100.0 * used / swap["total_bytes"] if isinstance(used, (int, float)) else None
        add("swap", "Swap", f"{pct:.0f}%（水位）" if pct is not None else WORD[UNCONFIRMED],
            _level(pct, warn=t["swap_percent_high"], alert=None,
                   margin=t["swap_percent_recovery_margin"], previous=previous.get("swap")),
            value=pct)

    psi = _present(summary, "psi.memory")
    if psi:
        some, full = _psi_avg60(psi.get("some")), _psi_avg60(psi.get("full"))
        some_level = _level(some, warn=t["psi_memory_some_avg60_percent_high"], alert=None,
                            margin=t["psi_memory_some_avg60_percent_recovery_margin"],
                            previous=previous.get("psi"))
        full_level = ALERT if full is not None and full >= t["psi_memory_full_avg60_percent_high"] else OK
        if some is not None or full is not None:
            add("psi", "内存压力",
                f"some {some if some is not None else '?'}% · full {full if full is not None else '?'}%",
                worst([some_level, full_level]))

    storage = _present(summary, "storage.readonly")
    for row in (storage or {}).get("filesystems") or []:
        total, avail = row.get("total_bytes"), row.get("available_bytes")
        if not isinstance(total, (int, float)) or total <= 0 or not isinstance(avail, (int, float)):
            continue
        pct = 100.0 * (total - avail) / total
        key = f"fs:{row['role']}"
        add(key, f"磁盘 {row['role']}", f"{pct:.0f}% · 余 {avail / GiB:.0f} GiB",
            _level(pct, warn=t["filesystem_percent_high"],
                   alert=t["filesystem_percent_critical"],
                   margin=t["filesystem_percent_recovery_margin"], previous=previous.get(key)),
            value=pct)

    gpu = _present(summary, "gpu.readonly")
    for row in (gpu or {}).get("gpus") or []:
        used, total = row.get("memory_used_bytes"), row.get("memory_total_bytes")
        if isinstance(used, (int, float)) and isinstance(total, (int, float)) and total > 0:
            # No threshold is declared for GPU memory: shown, never judged.
            add(f"gpu:{row.get('index')}", f"GPU{row.get('index')} 显存",
                f"{used / GiB:.0f}/{total / GiB:.0f} GiB", OK)

    services = _present(summary, "services")
    for row in (services or {}).get("services") or []:
        if row.get("state") == "FAILED":
            add(f"svc:{row['id']}", f"服务 {row['id']}", "已失败", ALERT)
        elif row.get("state") == "UNAVAILABLE":
            add(f"svc:{row['id']}", f"服务 {row['id']}", WORD[UNCONFIRMED], UNCONFIRMED)

    observed_at = (summary.get("source") or {}).get("received_at")
    level = worst(r["level"] for r in readings)
    return {"device_ref": device_ref, "name": display_name, "connected": True,
            "fresh": fresh, "readings": readings, "level": level,
            "observed_at": observed_at}


# -- level changes -> inbox events ----------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS resource_levels (
    device_ref TEXT NOT NULL,
    key        TEXT NOT NULL,
    level      TEXT NOT NULL,
    since      TEXT NOT NULL,
    PRIMARY KEY (device_ref, key)
);
"""


class LevelStore:
    def __init__(self, path: str | Path) -> None:
        self.path = private_file(path)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def previous(self, device_ref: str) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, level FROM resource_levels WHERE device_ref=?",
                                (device_ref,)).fetchall()
        return {row["key"]: row["level"] for row in rows}

    def since(self, device_ref: str, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT since FROM resource_levels WHERE device_ref=? AND key=?",
                               (device_ref, key)).fetchone()
        return row["since"] if row else None

    def set(self, device_ref: str, key: str, level: str, at: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO resource_levels(device_ref, key, level, since) "
                         "VALUES (?,?,?,?) ON CONFLICT(device_ref, key) DO UPDATE SET "
                         "level=excluded.level, since=excluded.since",
                         (device_ref, key, level, at))


def _kind(key: str) -> str:
    if key == "reach":
        return "node.unreachable"
    if key.startswith("svc:"):
        return "node.service_down"
    return "node.pressure"


def transitions(view: dict[str, Any], previous: dict[str, str]) -> list[dict[str, Any]]:
    """Level changes worth an event. UNCONFIRMED is neither raised nor cleared:
    a reading that went missing says nothing about whether the problem ended."""
    changes = []
    for reading in view["readings"]:
        before, now = previous.get(reading["key"], OK), reading["level"]
        if now == UNCONFIRMED or now == before:
            continue
        if before == UNCONFIRMED and now == OK:
            continue
        changes.append({"key": reading["key"], "label": reading["label"],
                        "text": reading["text"], "before": before, "now": now})
    return changes


def sweep(service: Any, principal: Any, inbox: Any, store: LevelStore,
          thresholds: dict[str, float], *, now_iso: str) -> list[dict[str, Any]]:
    """Read every company device, record level changes, return the views."""
    views = []
    for entry in service.registry.all():
        if getattr(entry, "ownership", None) != "company":
            continue
        device = entry.device_ref.ref
        try:
            summary = service.core_lite_status(principal, device)
        except Exception:  # noqa: BLE001 - one device's failure is that device's row
            summary = None
        previous = store.previous(device)
        view = device_view(device, getattr(entry, "display_name", None) or device, summary,
                           thresholds, previous, seen_before=bool(previous))
        views.append(view)
        for change in transitions(view, previous):
            recovered = change["now"] == OK
            kind = "node.recovered" if recovered else _kind(change["key"])
            inbox.record({
                # One fact per reading, level and moment: two readings that
                # change in the same sweep are two events, not one deduplicated.
                "event_id": hashlib.sha256(
                    f"{device}|{change['key']}|{change['now']}|{now_iso}".encode()).hexdigest(),
                "kind": kind,
                "level": "auto" if recovered else ("decide" if change["now"] == ALERT else "notify"),
                "device_ref": device, "project": None,
                "occurred_at": now_iso,
                "summary": f"{view['name']} · {change['label']}：{change['text']}"
                           f"（{WORD.get(change['before'], change['before'])}→{WORD[change['now']]}）",
                "evidence": f"{change['key']}|{change['now']}",
            })
            store.set(device, change["key"], change["now"], now_iso)
        for reading in view["readings"]:
            if reading["level"] != UNCONFIRMED and reading["key"] not in previous:
                store.set(device, reading["key"], reading["level"], now_iso)
    return views
