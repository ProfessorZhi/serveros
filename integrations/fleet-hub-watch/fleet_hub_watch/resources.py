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
    "psi_memory_full_avg60_percent_recovery_margin",
)


#: Full memory stall (every task waiting) that makes the hub push an alert card.
PSI_FULL_ALERT_PERCENT = 5.0

#: Consecutive failed reads before a device is called 失联.
#:
#: One failed read is not an outage. Every hub request shares one SSH tunnel
#: with the session reads of the same tick, so a big transcript being fetched
#: can push a small probe past its timeout; the next tick then finds the tunnel
#: free again and the reading returns. Reporting that as 失联 → 已恢复 turns a
#: busy tunnel into a page, twice an hour. Three in a row (≈90 s) still names a
#: real outage far sooner than a person notices it, and a one-off delay is
#: counted for the 日报 without interrupting anybody.
REACH_CONFIRMATIONS = 3


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


# The words a person reads. Owner, 2026-11-10: «Swap 水位», «内存压力 停顿»,
# «ROOT_FS» had to be translated before they could be read.
SWAP_LABEL = "交换区"
PSI_LABEL = "内存吃紧"
DISK_LABELS = {"ROOT_FS": "系统盘", "DATA_FS": "数据盘", "DATA": "数据盘", "HOME_FS": "主目录盘"}


def disk_label(role: str) -> str:
    return DISK_LABELS.get(str(role), f"磁盘 {role}")


def device_view(device_ref: str, display_name: str, summary: dict | None,
                thresholds: dict[str, float], previous: dict[str, str] | None = None,
                *, seen_before: bool = True) -> dict[str, Any]:
    """One device's readings for the card, each with its level. PURE."""
    previous = previous or {}
    readings: list[dict[str, Any]] = []

    def add(key, label, text, level, *, value=None, short=None):
        # `short` is the bare number for a tile or a one-line row, when `text`
        # is a sentence.
        readings.append({"key": key, "label": label, "text": text, "level": level,
                         "value": value, **({"short": short} if short else {})})

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
        add("swap", SWAP_LABEL, f"{pct:.0f}%" if pct is not None else WORD[UNCONFIRMED],
            _level(pct, warn=t["swap_percent_high"], alert=None,
                   margin=t["swap_percent_recovery_margin"], previous=previous.get("swap")),
            value=pct)

    psi = _present(summary, "psi.memory")
    if psi:
        some, full = _psi_avg60(psi.get("some")), _psi_avg60(psi.get("full"))
        some_level = _level(some, warn=t["psi_memory_some_avg60_percent_high"], alert=None,
                            margin=t["psi_memory_some_avg60_percent_recovery_margin"],
                            previous=previous.get("psi"))
        # `full` carries its own recovery margin: without one a reading that
        # hovers on the line opens and closes an alert every pass.
        # Owner, 2026-11-09: a 0.1-1% stall sent a red card. The contract's bar
        # (0.5%) is where the hub starts saying 注意 on 状态; a card is pushed
        # only from PSI_FULL_ALERT_PERCENT, a stall a person would feel.
        full_level = (OK if full is None else
                      _level(full, warn=t["psi_memory_full_avg60_percent_high"],
                             alert=max(PSI_FULL_ALERT_PERCENT, t["psi_memory_full_avg60_percent_high"]),
                             margin=t["psi_memory_full_avg60_percent_recovery_margin"],
                             previous=previous.get("psi")))
        if some is not None or full is not None:
            def pct(x):
                return "?" if x is None else f"{x:.1f}%"
            stall = pct(full if full is not None else some)
            add("psi", PSI_LABEL, f"程序有 {stall} 的时间在等内存",
                worst([some_level, full_level]), short=stall)

    storage = _present(summary, "storage.readonly")
    # The contract carries each storage block as `{"roles": [...]}` — a mapping
    # holding a role list, never a bare list (core-lite-status.v1.md, and the
    # readback validator that admits the document). Reading `filesystems` as if
    # it were the list iterated the mapping's own keys, so the first device that
    # reported storage at all took the whole sweep down with
    # `'str' object has no attribute 'get'` — a latent shape mismatch that no
    # test could see while the only CONNECTED device reported no storage.
    filesystems = ((storage or {}).get("filesystems") or {}).get("roles") or []
    for row in filesystems:
        total, avail = row.get("total_bytes"), row.get("available_bytes")
        if not isinstance(total, (int, float)) or total <= 0 or not isinstance(avail, (int, float)):
            continue
        pct = 100.0 * (total - avail) / total
        key = f"fs:{row['role']}"
        add(key, disk_label(row["role"]), f"{pct:.0f}% · 余 {avail / GiB:.0f} GiB",
            _level(pct, warn=t["filesystem_percent_high"],
                   alert=t["filesystem_percent_critical"],
                   margin=t["filesystem_percent_recovery_margin"], previous=previous.get(key)),
            value=pct)

    gpu = _present(summary, "gpu.readonly")
    for row in (gpu or {}).get("gpus") or []:
        used, total = row.get("memory_used_bytes"), row.get("memory_total_bytes")
        if isinstance(used, (int, float)) and isinstance(total, (int, float)) and total > 0:
            # No threshold is declared for GPU memory: shown, never judged.
            # The share is recorded so 「显卡」 questions get a curve.
            add(f"gpu:{row.get('index')}", f"GPU{row.get('index')} 显存",
                f"{used / GiB:.0f}/{total / GiB:.0f} GiB", OK, value=round(100.0 * used / total, 1))

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
CREATE TABLE IF NOT EXISTS reach_failures (
    device_ref TEXT PRIMARY KEY,
    count      INTEGER NOT NULL,
    code       TEXT,
    since      TEXT NOT NULL
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

    def note_failure(self, device_ref: str, code: str | None, at: str) -> int:
        """Count one failed read and return how many in a row this now is."""
        with self._connect() as conn:
            row = conn.execute("SELECT count, since FROM reach_failures WHERE device_ref=?",
                               (device_ref,)).fetchone()
            count = (row["count"] if row else 0) + 1
            since = row["since"] if row else at
            conn.execute("INSERT INTO reach_failures(device_ref, count, code, since) "
                         "VALUES (?,?,?,?) ON CONFLICT(device_ref) DO UPDATE SET "
                         "count=excluded.count, code=excluded.code, since=excluded.since",
                         (device_ref, count, code, since))
        return count

    def clear_failure(self, device_ref: str) -> None:
        """The tunnel answered again: the streak (and any delay it recorded) is over."""
        with self._connect() as conn:
            conn.execute("DELETE FROM reach_failures WHERE device_ref=?", (device_ref,))


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


def _reason(exc: BaseException) -> str:
    """A short name for why a read failed, for the event evidence.

    The provider's own code (TIMEOUT, UNREACHABLE, …) is the useful one; the
    exception class is the fallback for anything that does not carry one.
    """
    code = getattr(exc, "code", None)
    if code:
        return str(code)
    return type(exc).__name__


def _read_device(service: Any, principal: Any,
                 device: str) -> tuple[dict[str, Any] | None, str | None]:
    """The reading the card shows for one machine, and why it was missing.

    A card is one machine, but a machine can hold several runtime bindings — one
    per OS account — and `core_lite_status` asks exactly one of them. Asking
    only the base runtime made a device whose sole binding is a second account
    read as "not connected" while its Core was answering the whole time; the
    card said "no reading" about a machine that had one, just not under the name
    that was asked.

    So the runtimes are asked in the order the host holds them (base first,
    which is what every device with a single runtime gets, unchanged) and the
    first CONNECTED answer is the machine's reading. If none is CONNECTED the
    FIRST summary is kept rather than the last: it names the reason for the
    runtime a caller would have asked for by default, and a reason is more use
    than whichever runtime happened to be sorted last.

    The second half of the answer is the reason a read RAISED, when one did.
    It used to be swallowed by `except Exception: continue`, so every outage
    looked identical in the event evidence — `reach|ALERT` and nothing about
    which failure it was. A silence the hub cannot name is a silence it cannot
    tell apart from the tunnel being busy (see REACH_CONFIRMATIONS).
    """
    runtimes = service.bound_runtimes(device)
    if not runtimes:
        # No binding at all. Ask the default so the row still carries a named
        # gap (NOT_CONFIGURED) instead of silence.
        try:
            return service.core_lite_status(principal, device), None
        except Exception as exc:  # noqa: BLE001 - one device's failure is that device's row
            return None, _reason(exc)
    first: dict[str, Any] | None = None
    reason: str | None = None
    for runtime in runtimes:
        try:
            summary = service.core_lite_status(principal, device, runtime=runtime)
        except Exception as exc:  # noqa: BLE001 - one runtime's failure is that runtime's
            if reason is None:
                reason = _reason(exc)
            continue
        if summary is None:
            # A source that answered with nothing is not a reading; it is the
            # absence of one and must not outrank a runtime that did answer.
            continue
        if first is None:
            first = summary
        if summary.get("connected") == "CONNECTED":
            return summary, None
    if first is None and reason is None:
        # Every runtime answered with nothing at all: silence, not an exception.
        reason = "NO_READING"
    return first, reason


def _hold_reach(view: dict[str, Any], previous: dict[str, str], count: int) -> None:
    """A failed read inside the confirmation window keeps the level it had.

    The reading still says the read did not come back — that is the honest
    thing to show — but its LEVEL is the one before the miss, so the view does
    not transition and no event is recorded. A tunnel that drops one read in
    the middle of a big transcript is counted here and never becomes a page.
    """
    for reading in view["readings"]:
        if reading["key"] != "reach":
            continue
        reading["text"] = f"读数中断（{count}/{REACH_CONFIRMATIONS} 次）"
        reading["level"] = previous.get("reach", UNCONFIRMED)
    view["level"] = worst(r["level"] for r in view["readings"])


def sweep(service: Any, principal: Any, inbox: Any, store: LevelStore,
          thresholds: dict[str, float], *, now_iso: str) -> list[dict[str, Any]]:
    """Read every company device, record level changes, return the views."""
    views = []
    for entry in service.registry.all():
        if getattr(entry, "ownership", None) != "company":
            continue
        device = entry.device_ref.ref
        summary, reason = _read_device(service, principal, device)
        previous = store.previous(device)
        view = device_view(device, getattr(entry, "display_name", None) or device, summary,
                           thresholds, previous, seen_before=bool(previous))
        # `reason` is set only when a read RAISED. A device that answered and
        # said it is not connected is a fact, not a miss, and is reported at
        # once: the confirmation window is for reads that never came back.
        reason_code: str | None = None
        if view["connected"]:
            store.clear_failure(device)
        elif reason is not None:
            count = store.note_failure(device, reason, now_iso)
            if count < REACH_CONFIRMATIONS:
                _hold_reach(view, previous, count)
            else:
                reason_code = reason
        views.append(view)
        for change in transitions(view, previous):
            recovered = change["now"] == OK
            kind = "node.recovered" if recovered else _kind(change["key"])
            evidence = f"{change['key']}|{change['now']}"
            if reason_code and change["key"] == "reach":
                # A third field the readers below tolerate: which failure it was.
                evidence += f"|{reason_code}"
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
                "evidence": evidence,
            })
            store.set(device, change["key"], change["now"], now_iso)
        for reading in view["readings"]:
            if reading["level"] != UNCONFIRMED and reading["key"] not in previous:
                store.set(device, reading["key"], reading["level"], now_iso)
    return views
