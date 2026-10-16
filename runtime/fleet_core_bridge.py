#!/usr/bin/env python3
"""The Core reading reaches the card: an admitted readback becomes ONE device.

WHAT THIS CLOSES
----------------
`fleet_monitor_source.fleet_model` could already carry a peer device, but only as
an UNKNOWN row — nothing turned a peer's Core reading into readings. This module
is that missing hop and nothing else: it takes the summary that
`fleet_mcp.core_lite_source.admit_core_lite` produced for one device and returns
the DEVICE ENTRY `fleet_model` consumes, so the peer arrives over the SAME
inventory/coverage/audience pipeline and is rendered by the SAME V13 renderer.
There is no second model builder, no second renderer and no second ledger here.

WHAT IT WILL NOT TRUST, AND WHY EACH IS A DIFFERENT ANSWER
----------------------------------------------------------
The readback already decided attribution; this module re-states the checks it
depends on rather than inheriting them by hope, because a caller can hand it any
dict:

  * `route.state != "VERIFIED"` — nothing authenticated this reading to this
    device. Rendered UNKNOWN with the route's own reason, so «nobody wired a
    source» and «the route reached another machine» stay different sentences.
  * `device_ref` disagreement — the summary is for a different device. REFUSED
    (raised), because that is a caller's bug and filing it under this device
    would be the substitution the route check exists to prevent.
  * `live.accepted is not True` — the reading did not earn the live claim.
    UNKNOWN, never the numbers.
  * freshness not FRESH — STALE or UNKNOWN; a reading whose age nobody can place
    is not a fresh one.
  * a `received_at` in the FUTURE — refused. A clock that has not happened is
    not evidence about now.
  * `principal.state == "UNKNOWN"` — nobody established which account this
    reading belongs to, so it is not shown as this device's numbers.

UNKNOWN IS IMMUTABLE. A block the readback marked `UNAVAILABLE`, a field it did
not carry, and an unreadable number all render as 未读到/无法确认 WITH the reason
the reader wrote — never `0`, never an empty reading that reads as healthy. That
is the same rule `fleet_monitor_source` applies to the native envelope, applied
to this producer.

NO IDENTITY, NO ARGV, NO ENV. The readings are counters and state words about a
MACHINE. Nothing here reads, copies or renders another account's uid, command
line, environment, path or token, and the fields this module even looks for are
a closed list: anything else in the readback is ignored rather than carried.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


monitor = _load("serveros_fleet_monitor_card", _HERE / "fleet_monitor_card.py")
fleet_source = _load("serveros_fleet_monitor_source", _HERE / "fleet_monitor_source.py")

#: The readback the admitted summary declares. A source that reads a different
#: shape is not this device's reading, whatever else it got right.
CORE_READBACK_SCHEMA = "serveros/fleet-core-lite-readback.v1"
DEVICE_ENTRY_SCHEMA = "serveros/fleet-monitor-device.v1"

#: The route states a summary may carry, mirrored from
#: `fleet_mcp/core_lite_source.ROUTE_STATES`. Only VERIFIED has a document read
#: over an authenticated route that names this device.
ROUTE_VERIFIED = "VERIFIED"
ROUTE_STATES = ("NOT_CONFIGURED", "SOURCE_FAILED", "UNAUTHENTICATED",
                "DEVICE_MISMATCH", "NO_DOCUMENT", ROUTE_VERIFIED)

#: The connected word, mirrored from the same module: UNKNOWN is "nothing was
#: established", NOT_CONNECTED is "something was established and it was not this".
CONNECTED_UNKNOWN = "UNKNOWN"
CONNECTED_NOT_CONNECTED = "NOT_CONNECTED"
CONNECTED_STATES = (CONNECTED_UNKNOWN, CONNECTED_NOT_CONNECTED, "CONNECTED")

OBSERVATION_PRESENT = "PRESENT"
OBSERVATION_UNAVAILABLE = "UNAVAILABLE"

#: The observation names this bridge reads. A name outside this list is not
#: carried, which is what keeps a document from growing a field this file would
#: publish without anybody deciding to.
OBSERVATIONS_READ = ("cpu", "memory", "swap", "psi.cpu", "psi.memory", "psi.io",
                     "storage.readonly", "services", "gpu.readonly")

#: Service states the readback projects, and the severity each earns. A service
#: that is RUNNING is not thereby healthy — this map only decides how loud the
#: word is, and the word itself is the producer's.
SERVICE_STATE_SEVERITY = {
    "ACTIVE": "OK", "RUNNING": "OK", "READY": "OK",
    "ACTIVATING": "ATTENTION", "DEACTIVATING": "ATTENTION",
    "RELOADING": "ATTENTION", "INACTIVE": "ATTENTION",
    "FAILED": "CRITICAL", "DEAD": "CRITICAL",
}


class CoreRefused(RuntimeError):
    """A summary this bridge will not attribute to this device."""


# ---------------------------------------------------------------------------
# small readers — every one of them answers "unknown" rather than guessing
# ---------------------------------------------------------------------------

def _parse(value):
    """An ISO instant with a zone, or None. A naive stamp is NOT a time."""
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _gib(value):
    number = _num(value)
    return None if number is None else f"{number / 1024 ** 3:.1f} GiB"


def _pct(used, total):
    u, t = _num(used), _num(total)
    if u is None or t is None or t == 0:
        return None
    return round(u / t * 100, 1)


def _reading_value(block):
    """`(value_dict, reason)`. A PRESENT block carries an object; anything else
    is a reason, so a malformed value never becomes an empty but present one."""
    if not isinstance(block, dict):
        return None, "the observation is not an object"
    if block.get("state") == OBSERVATION_PRESENT:
        value = block.get("value")
        if isinstance(value, dict):
            return value, None
        return None, "a PRESENT observation carried no object value"
    reason = block.get("reason") or "the observation did not carry a value"
    return None, str(reason)


# ---------------------------------------------------------------------------
# the verdict: may these numbers be shown as THIS device's, and how fresh
# ---------------------------------------------------------------------------

def core_verdict(summary, *, device_ref, now, stale_after_s):
    """`(state, why, observed_at)` for one admitted summary. PURE.

    `state` is one of the card module's KNOWN/UNKNOWN/STALE. KNOWN is returned
    only when the route authenticated to THIS device, the live claim was
    accepted, the reading is FRESH, and an account was established. Everything
    else answers UNKNOWN or STALE with the sentence that says which of those
    failed, so a reader is never told "we do not know" without being told why.
    """
    if not isinstance(summary, dict):
        raise CoreRefused(f"a summary is an object, got {type(summary).__name__}")
    if summary.get("schema") != CORE_READBACK_SCHEMA:
        raise CoreRefused(
            f"schema {summary.get('schema')!r} is not {CORE_READBACK_SCHEMA!r}")
    if summary.get("device_ref") != device_ref:
        # The route/readback already refuses a mismatch; this bridge refuses it
        # again rather than inheriting the check by hope, because a caller can
        # hand this function any dict at all.
        raise CoreRefused(
            "the summary names a different device than the one it would be "
            "filed under; a reading filed under the wrong device is the "
            "substitution the route check exists to prevent")

    route = summary.get("route") or {}
    if not isinstance(route, dict) or route.get("state") != ROUTE_VERIFIED:
        state = (route or {}).get("state")
        reason = (route or {}).get("reason") or "no authenticated route carried this reading"
        return monitor.STATE_UNKNOWN, f"{state or 'NO_ROUTE'}：{reason}", None

    live = summary.get("live") or {}
    if not isinstance(live, dict) or live.get("accepted") is not True:
        why = (live or {}).get("why") if isinstance(live, dict) else None
        return (monitor.STATE_UNKNOWN,
                f"读数没有取得 live 声明（{why or 'live 未被接受'}）", None)

    principal = summary.get("principal") or {}
    if not isinstance(principal, dict) or principal.get("state") == "UNKNOWN":
        # Which account this reading belongs to was never established. The
        # counters might be right; publishing them as this device's is not.
        return (monitor.STATE_UNKNOWN,
                "没有确立该读数属于哪个账户（principal.state=UNKNOWN）", None)

    source = summary.get("source") or {}
    received = _parse((source or {}).get("received_at"))
    clock = _parse(now)
    if received is None or clock is None:
        return (monitor.STATE_UNKNOWN,
                "读数没有可解析的接收时间，无法判定新鲜度", None)
    if (received - clock).total_seconds() > 0:
        # A stamp in the future is refused rather than clamped: a clock that has
        # not happened is not evidence about now, and treating it as now would
        # make any document fresh forever.
        return (monitor.STATE_UNKNOWN,
                "读数的接收时间在未来，拒绝把它当作现在的观测", None)

    observed_at = (source or {}).get("received_at")
    freshness = summary.get("freshness") or {}
    age = (clock - received).total_seconds()
    if freshness.get("state") != "FRESH" or age > stale_after_s:
        word = freshness.get("state") or "NO_TIMESTAMP"
        return (monitor.STATE_STALE if age > stale_after_s else monitor.STATE_UNKNOWN,
                f"新鲜度 {word}：接收于 {observed_at}，距今 {int(age // 60)} 分钟，"
                f"超过上限 {int(stale_after_s // 60)} 分钟", observed_at)
    return monitor.STATE_KNOWN, None, observed_at


# ---------------------------------------------------------------------------
# observations → readings
# ---------------------------------------------------------------------------

def core_readings(summary, *, state, why, observed_at):
    """The admitted observations as card readings, under ONE freshness word.

    FAIL-CLOSED, exactly as the native bridge is: when `state` is not KNOWN
    every reading keeps a word with a reason and no number, because a number
    beside a stale or unknown reading is read as current.
    """
    observations = summary.get("observations")
    if not isinstance(observations, dict):
        return [monitor.unobserved("主机读数", reason="summary 没有 observations 对象",
                                   kind="host")]
    out = []

    def emit(label, value, *, unit="", note="", kind="reading", severity="OK"):
        if state == monitor.STATE_KNOWN:
            return monitor.reading(label, value, unit=unit, note=note,
                                   observed_at=observed_at, severity=severity,
                                   kind=kind)
        if state == monitor.STATE_STALE:
            return monitor.stale(monitor.reading(label, value, unit=unit, note=note,
                                                 observed_at=observed_at,
                                                 severity=severity, kind=kind),
                                 reason=why)
        return monitor.unobserved(label, reason=why, kind=kind)

    def block(name):
        return observations.get(name) if name in observations else None

    def unavailable(label, name, kind="reading"):
        out.append(monitor.unobserved(
            label, reason=(f"{name} 未读到（{_reading_value(block(name))[1]}）"
                           if block(name) else f"{name} 未在读数中出现"), kind=kind))

    # cpu -------------------------------------------------------------------
    value, reason = _reading_value(block("cpu"))
    cpu_pct = _num((value or {}).get("used_percent"))
    if cpu_pct is not None:
        out.append(emit("CPU", cpu_pct, unit="%", note="整机 · Core 只读读数", kind="cpu"))
    else:
        unavailable("CPU", "cpu", kind="cpu")

    # memory ----------------------------------------------------------------
    value, reason = _reading_value(block("memory"))
    if value is not None:
        total = value.get("total_bytes")
        available = value.get("available_bytes")
        used = (total - available
                if isinstance(total, (int, float)) and isinstance(available, (int, float))
                else None)
        percent = value.get("used_percent")
        if percent is None:
            percent = _pct(used, total)
        note = f"可用 {_gib(available) or '未读到'} / 共 {_gib(total) or '未读到'}"
        out.append(emit("内存", percent if percent is not None else "—",
                        unit="%" if percent is not None else "", note=note, kind="memory"))
    else:
        unavailable("内存", "memory", kind="memory")

    # swap: STOCK and FLOW are different facts and are not merged ------------
    value, reason = _reading_value(block("swap"))
    if value is not None:
        used, total = value.get("used_bytes"), value.get("total_bytes")
        percent = _pct(used, total)
        out.append(emit("Swap 存量", percent if percent is not None else "—",
                        unit="%" if percent is not None else "",
                        note=f"已用 {_gib(used) or '未读到'} / 共 {_gib(total) or '未读到'}"
                             f"（存量，不是换页速率）", kind="swap"))
        pin, pout = _num(value.get("pages_in_per_second")), _num(value.get("pages_out_per_second"))
        if pin is not None or pout is not None:
            out.append(emit("Swap 换页流量", f"入 {pin if pin is not None else '未读到'}"
                                            f" · 出 {pout if pout is not None else '未读到'}",
                            note="页/秒（流量口径，与存量分开）", kind="swap"))
    else:
        # Tokyo's swap is one of the blocks the contract may answer UNAVAILABLE
        # inline; that is carried as an unmeasured reading, never as 0.
        unavailable("Swap", "swap", kind="swap")

    # psi -------------------------------------------------------------------
    for name, label in (("psi.cpu", "PSI CPU"), ("psi.memory", "PSI 内存"),
                        ("psi.io", "PSI IO")):
        value, reason = _reading_value(block(name))
        if value is None:
            unavailable(label, name, kind="psi")
            continue
        some, full = value.get("some"), value.get("full")

        def word(line):
            if isinstance(line, dict):
                for key in ("avg10", "avg60", "avg300"):
                    if _num(line.get(key)) is not None:
                        return f"{key}={line.get(key)}%"
                return "已读到"
            return str(line) if line is not None else "未读到"

        out.append(emit(label, f"some {word(some)} · full {word(full)}",
                        note="压力失速（PSI），越接近 100% 越无法推进",
                        kind="psi"))

    # storage ---------------------------------------------------------------
    value, reason = _reading_value(block("storage.readonly"))
    if value is None:
        unavailable("磁盘", "storage.readonly", kind="filesystem")
        unavailable("磁盘吞吐", "storage.readonly", kind="disk-io")
    else:
        filesystems = value.get("filesystems") or {}
        for row in (filesystems.get("roles") or []):
            if not isinstance(row, dict):
                continue
            name = row.get("role") or "?"
            percent = _pct(
                (row.get("total_bytes") or 0) - (row.get("available_bytes") or 0),
                row.get("total_bytes"))
            out.append(emit(f"磁盘 {name}", percent if percent is not None else "—",
                            unit="%" if percent is not None else "",
                            note=f"可用 {_gib(row.get('available_bytes')) or '未读到'}"
                                 f"（f_bavail）", kind="filesystem"))
        disk_io = value.get("disk_io") or {}
        for row in (disk_io.get("roles") or []):
            if not isinstance(row, dict):
                continue
            name = row.get("role") or "?"
            out.append(emit(
                f"磁盘吞吐 {name}",
                f"读 {_rate(row.get('read_bytes_per_second'))} · "
                f"写 {_rate(row.get('write_bytes_per_second'))}",
                note="本机设备吞吐，非公网", kind="disk-io"))

    # services --------------------------------------------------------------
    value, reason = _reading_value(block("services"))
    if value is None:
        unavailable("Core 服务", "services", kind="service")
    else:
        for row in (value.get("services") or []):
            if not isinstance(row, dict):
                continue
            ident = row.get("id") or row.get("unit") or "?"
            word = str(row.get("state") or "UNKNOWN").upper()
            severity = SERVICE_STATE_SEVERITY.get(word, "ATTENTION")
            out.append(emit(f"服务 {ident}", word, severity=severity,
                            note=f"unit {row.get('unit') or '未读到'}（Core 只读观测）",
                            kind="service"))

    # gpu -------------------------------------------------------------------
    value, reason = _reading_value(block("gpu.readonly"))
    if value is None:
        unavailable("GPU", "gpu.readonly", kind="gpu")
    else:
        for row in (value.get("gpus") or []):
            if not isinstance(row, dict):
                continue
            tag = row.get("label") or f"#{row.get('index')}"
            util = _num(row.get("gpu_util_percent"))
            if util is not None:
                out.append(emit(f"GPU {tag} 利用率", util, unit="%",
                                note="卡级计数（低利用率不等于服务挂了）", kind="gpu"))
            used, total = row.get("memory_used_bytes"), row.get("memory_total_bytes")
            if _num(total) is not None:
                out.append(emit(f"GPU {tag} VRAM",
                                f"{_gib(used) or '未读到'} / {_gib(total) or '未读到'}",
                                note="卡级显存", kind="gpu"))
            temp = _num(row.get("temperature_c"))
            if temp is not None:
                out.append(emit(f"GPU {tag} 温度", temp, unit="°C", kind="gpu"))
        if not (value.get("gpus") or []):
            unavailable("GPU", "gpu.readonly", kind="gpu")
        else:
            out.append(emit("GPU 采样时刻", str(value.get("sample_time") or "未读到"),
                            note="卡级计数的采样时间", kind="gpu"))
    return out


def _rate(value):
    """A byte-per-second figure in a unit a person reads. Not a percent."""
    size = _num(value)
    if size is None:
        return "未读到"
    for unit in ("B/s", "KiB/s", "MiB/s", "GiB/s"):
        if abs(size) < 1024 or unit == "GiB/s":
            return f"{size:.1f} {unit}"
        size /= 1024
    return "未读到"


# ---------------------------------------------------------------------------
# the device entry
# ---------------------------------------------------------------------------

def device_from_core_readback(summary, *, device_ref, display_name=None,
                              platform=None, now, stale_after_s=1800,
                              observed_ever=True):
    """One admitted Core summary -> the device entry `fleet_model` consumes.

    Raises `CoreRefused` for a summary this bridge will not file under this
    device (wrong schema, wrong device, not an object). Every OTHER refusal is a
    device state and comes back as an UNKNOWN/STALE row with its reason, so a
    device whose Core is not wired still appears on the card — coverage is a
    property of the declaration, not of what answered.
    """
    state, why, observed_at = core_verdict(summary, device_ref=device_ref,
                                           now=now, stale_after_s=stale_after_s)
    readings = core_readings(summary, state=state, why=why, observed_at=observed_at)
    route = summary.get("route") or {}
    reach = ({"state": monitor.STATE_KNOWN, "observed_at": observed_at}
             if state == monitor.STATE_KNOWN else
             {"state": state, "reason": why})
    # Validated by the same seam every other producer goes through, so a reading
    # without provenance is refused here rather than rendered as healthy. That
    # seam returns the CARD's device shape, which is narrower than the model's:
    # it keeps device_ref/display_name/platform/reachability/readings and drops
    # everything else, so the two fields the model needs are re-attached below
    # rather than assumed to survive.
    entry = fleet_source.accept_device_snapshot({
        "schema": DEVICE_ENTRY_SCHEMA,
        "device_ref": device_ref,
        "display_name": display_name or device_ref,
        "platform": platform or "未知",
        "reachability": reach,
        "readings": readings,
    })
    entry["accounts"] = []
    # A device the Core route did not authenticate to has never been observed BY
    # THIS ROUTE, which is a coverage gap and renders grey; a device that
    # answered before and is silent now is a red outage. The route state is the
    # fact that separates them, and the caller may only VETO a claim of prior
    # observation, never manufacture one.
    entry["observed_ever"] = bool(observed_ever
                                  and route.get("state") == ROUTE_VERIFIED)
    return entry


# ---------------------------------------------------------------------------
# the model source the runner injects
# ---------------------------------------------------------------------------

def peer_model_source(core_readbacks, *, local_device_ref, local_display_name,
                      local_platform, peers, archive=None, stale_after_s=1800):
    """`model_source(at) -> (model, rows)` over one local host and Core peers.

    `core_readbacks(at) -> {device_ref: admitted_summary}` is the CALLER's
    reader: this module does not open a socket, run a command or build a URL —
    the Core route is that provider's business, and the seam here is deliberately
    too narrow to grow an egress of its own (the same boundary
    `fleet_mcp.core_lite_source` draws for itself). A device the reader does not
    name is carried UNKNOWN by the inventory, never dropped.

    `archive`, when given, is the shared `fleet_monitor_source.TrendArchive`: the
    Core readings are recorded there and the SAME store is what a trend is later
    drawn from, so the card and the hourly report cannot disagree about it.
    """
    card_source = _load("serveros_runtime_card_source", _HERE / "card_source.py")
    pairs = [(ref, name) for ref, name in peers]

    def source(*, at):
        snapshots = [(local_device_ref,
                      _normalise_gaps(card_source.snapshot(card_source.gather(), at=at)))]
        names = {local_device_ref: local_display_name}
        platforms = {local_device_ref: local_platform}
        entries = []
        readbacks = core_readbacks(at=at) if callable(core_readbacks) else dict(core_readbacks)
        for ref, name in pairs:
            names[ref] = name
            summary = readbacks.get(ref)
            if summary is None:
                continue  # the inventory carries it as an UNKNOWN row
            try:
                entry = device_from_core_readback(
                    summary, device_ref=ref, display_name=name, now=at,
                    stale_after_s=stale_after_s,
                    observed_ever=bool((summary.get("route") or {}).get("state") == ROUTE_VERIFIED))
            except CoreRefused:
                continue  # not this device's reading; the inventory row stands
            entries.append(entry)
            if archive is not None:
                for reading in entry.get("readings") or []:
                    if reading.get("state") != monitor.STATE_KNOWN:
                        continue
                    value = _num(reading.get("value"))
                    if value is None:
                        continue
                    archive.record({"device_ref": ref, "label": reading.get("label"),
                                    "value": value,
                                    "observed_at": reading.get("observed_at"),
                                    "attributed": True, "source": "core"})
        model = fleet_source.fleet_model(
            snapshots=snapshots, entries=entries, now=at, sample=False,
            names=names, platforms=platforms, stale_after_s=stale_after_s)
        return model, []

    return source


def _normalise_gaps(snapshot):
    """`card_source.snapshot()`'s sentence gaps -> the mapping `fleet_model` reads.

    The same join `card_surface_runner` makes, kept here as well so this adapter
    does not depend on that module's internals: a string gap is a true statement
    and a mapping is a true statement, and this is the place that knows both.
    """
    gaps = snapshot.get("gaps") if isinstance(snapshot, dict) else None
    if not isinstance(gaps, list):
        return snapshot
    fixed = []
    for gap in gaps:
        if isinstance(gap, dict):
            fixed.append(gap)
        elif isinstance(gap, str):
            fixed.append({"read": "", "why": gap})
    out = dict(snapshot)
    out["gaps"] = fixed
    return out
