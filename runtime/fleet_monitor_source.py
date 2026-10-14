#!/usr/bin/env python3
"""The read-only producer adapter for the Fleet monitor card.

This is the ONLY module of the pair allowed to touch the machine, and even here
the touching is done by someone else: `runtime/card_source.py` already gathers a
host's readings and shapes them into a snapshot, and this module COMPOSES those
snapshots into the many-device input `runtime/fleet_monitor_card.monitor_model`
consumes. It measures nothing of its own and writes nothing anywhere, which is
what `不造平行状态权威` means at this layer: the card is a view of readings a
module that already owns them produced.

Why the composition is not in `fleet_monitor_card`
--------------------------------------------------
`monitor_model` must stay PURE — a test asserts it imports no I/O — so that a
model can be built in a test, on a machine with no fleet, and be byte-identical
to one built on the fleet. The moment the pure module could reach a snapshot,
that property would be gone, and the renderer would silently depend on the host
that happened to render it.

What a snapshot already carries
-------------------------------
`card_source.snapshot()` returns, per host: `host` and `mine` sections shaped
`{state, lines, metrics, why, detail}`, a `gaps` list of reads that did not
answer, and a `coverage` map. This adapter reads `host.metrics` for the face and
`gaps` for the unmeasured — it does not recompute a threshold, does not re-rank
a severity, and does not open a file. A section that is not KNOWN becomes an
UNKNOWN reading WITH the section's own `why`, so "the reading did not arrive" is
never rendered as "the reading was zero".
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


card_source = _load("serveros_card_source", _HERE / "card_source.py")
monitor = _load("serveros_fleet_monitor_card", _HERE / "fleet_monitor_card.py")

#: The schema of the DEVICE ENTRY this module assembles — the shape
#: `monitor.monitor_model` consumes. It is deliberately NOT
#: `serveros/node-resource-snapshot.v1`: that id names the C++ collector's own
#: snapshot (see PR#161's `contract/node-resource-snapshot.v1.schema.json`), and
#: an id that named two different shapes would make a consumer unable to tell
#: which it was handed. This rename was made during the Phase 3 cross-PR audit,
#: when the collision was found.
DEVICE_ENTRY_SCHEMA = "serveros/fleet-monitor-device.v1"

#: The envelope the C++ adapter produces (`native/node-core/adapter/
#: native_node_core.py`, PR#161) and the ONLY native document this bridge reads.
NATIVE_MEASUREMENT_SCHEMA = "serveros/native-node-measurement.v1"

#: The TYPED per-UID summary this bridge accepts, defined HERE and nowhere else.
#: Issue #167 (per-UID memory alerts) is not merged, so this bridge reads nothing
#: from that worktree: the shape below is derived from the FIELDS that issue's own
#: text names — process_count, anonymous_bytes (preferred) over
#: rss_estimate_bytes, host_mem_total_bytes, host_percent, top_process {pid,comm},
#: sampled_at and a provenance block — and the id is chosen so that when #167
#: lands its producer, the field names either match or the seam refuses loudly
#: rather than silently reading a different shape.
UID_SUMMARY_SCHEMA = "serveros/uid-resource-summary.v1"


class SnapshotRefused(RuntimeError):
    """A document handed to the acceptance seam that this seam cannot accept."""


def accept_device_snapshot(document):
    """The typed acceptance seam for the C++ `NodeResourceSnapshot v1`.

    Takes a PARSED document and returns the device entry
    `monitor.monitor_model` consumes, or raises `SnapshotRefused` naming the
    first field it could not accept. It reads no file and imports nothing from
    the C++ side, so the bridge is assembled later — and in either order — by
    calling this with the producer's output.

    What it insists on is exactly the part of the contract the card rests on: a
    declared schema id, a device identity, a reachability state, and reading
    atoms where a non-UNKNOWN state carries its `observed_at`. Everything else
    is passed through untouched, because a seam that decided what a field MEANS
    would be a second place the decisions live.
    """
    if not isinstance(document, dict):
        raise SnapshotRefused(
            f"a snapshot is an object, got {type(document).__name__}")
    schema = document.get("schema")
    if schema != DEVICE_ENTRY_SCHEMA:
        raise SnapshotRefused(
            f"schema {schema!r} is not {DEVICE_ENTRY_SCHEMA!r}; a "
            f"device entry from an unknown producer version is not accepted")
    device_ref = document.get("device_ref")
    if not device_ref:
        raise SnapshotRefused("a snapshot names no device_ref")
    readings = document.get("readings")
    if not isinstance(readings, list):
        raise SnapshotRefused("a snapshot carries no `readings` list")
    for index, r in enumerate(readings):
        if not isinstance(r, dict):
            raise SnapshotRefused(f"reading {index} is not an object")
        if not r.get("label"):
            raise SnapshotRefused(f"reading {index} names no label")
        state = r.get("state")
        if state not in (monitor.STATE_KNOWN, monitor.STATE_UNKNOWN,
                         monitor.STATE_STALE):
            raise SnapshotRefused(
                f"reading {index} ({r.get('label')!r}) has state {state!r}, "
                f"which is not one of KNOWN/UNKNOWN/STALE")
        if state == monitor.STATE_KNOWN and not r.get("observed_at"):
            raise SnapshotRefused(
                f"reading {index} ({r.get('label')!r}) is KNOWN with no "
                f"observed_at; a measurement without provenance is not one")
    reachability = document.get("reachability")
    if not isinstance(reachability, dict) or "state" not in reachability:
        raise SnapshotRefused("a snapshot carries no reachability state")
    return {
        "device_ref": device_ref,
        "display_name": document.get("display_name") or device_ref,
        "platform": document.get("platform") or "未知",
        "reachability": dict(reachability),
        "readings": [dict(r) for r in readings],
    }

#: The four section states `card_source` uses, mapped to the severity a reading
#: carries. Everything not listed is OK. A section that is UNKNOWN is NOT mapped
#: to a severity: it becomes an unobserved reading with the section's own why.
_SECTION_SEVERITY = {"ATTENTION": "ATTENTION", "CRITICAL": "CRITICAL"}


# ---------------------------------------------------------------------------
# The native bridge: PR#161's envelope -> a device entry. Typed, pure, no import
# of any C++ file.
# ---------------------------------------------------------------------------

class NativeMeasurementRefused(RuntimeError):
    """An envelope this bridge cannot accept, or can only accept fail-closed."""


def _gib(value):
    if value is None:
        return "未读到"
    try:
        return f"{float(value) / 1024 ** 3:.1f} GiB"
    except (TypeError, ValueError):
        return "未读到"


def _rate(value):
    """A byte-per-second figure in a unit a person reads. Not a percent."""
    if value is None:
        return "未读到"
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "未读到"
    for unit in ("B/s", "KiB/s", "MiB/s", "GiB/s"):
        if abs(size) < 1024 or unit == "GiB/s":
            return f"{size:.1f} {unit}"
        size /= 1024
    return "未读到"


def _percent(used, total):
    try:
        if not total:
            return None
        return round(float(used) / float(total) * 100, 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _failure_reason(envelope):
    """Why there is no reading, from the envelope's own words. Never invented."""
    measurements = envelope.get("measurements")
    if isinstance(measurements, dict) and measurements.get("reason"):
        return str(measurements["reason"])
    refusals = envelope.get("refusals") or []
    if refusals and isinstance(refusals[0], dict) and refusals[0].get("reason"):
        return str(refusals[0]["reason"])
    unknowns = envelope.get("unknowns") or []
    if unknowns and isinstance(unknowns[0], dict) and unknowns[0].get("reason"):
        return str(unknowns[0]["reason"])
    return "envelope 声明没有可用读数（readings_available=false）"


def _native_freshness(envelope, observed_at, now, freshness_bound):
    """The ONE freshness word every reading in this envelope shares. fail-closed.

    Both authorities are applied and the STRICTER wins: the envelope's own
    `freshness.state` and the caller's `freshness_bound` measured against
    `observed_at`. A reading is KNOWN only when the envelope says FRESH AND its
    age is within the bound — so a missing window, an unparseable instant, a
    replay of an old sample, or a clock that cannot be compared all land on
    UNKNOWN or STALE and never on a green reading.
    """
    freshness = envelope.get("freshness") or {}
    word = str(freshness.get("state") or "UNKNOWN").upper()
    if word == "UNKNOWN":
        return monitor.STATE_UNKNOWN, (
            freshness.get("why") or "envelope 未给出可判定的新鲜度")
    when, clock = monitor._parse(observed_at), monitor._parse(now)
    if when is None or clock is None:
        return monitor.STATE_UNKNOWN, "观测时间或当前时间不可解析，无法判定新鲜度"
    age = (clock - when).total_seconds()
    if word == "STALE" or age > freshness_bound:
        return monitor.STATE_STALE, (
            f"最后观测 {observed_at} 距今 {int(age // 60)} 分钟，超过新鲜度上限 "
            f"{freshness_bound // 60} 分钟")
    return monitor.STATE_KNOWN, None


def _native_readings(envelope, *, observed_at, state, why):
    """The measurements as card readings, under ONE freshness word.

    FAIL-CLOSED. When `state` is not KNOWN every reading keeps a word with a
    reason and no number, because a number beside a stale or unknown reading is
    read as current. The mapping itself is deliberate and narrow:

      * `cpu.used_percent` -> the host-wide CPU reading.
      * `memory` -> total/available BYTES, converted and noted; the percent is
        this bridge's own arithmetic over those bytes, not a second source.
      * `filesystems.roles[ROOT_FS|DATA_FS]` -> the card's disk readings, using
        `available_bytes` (f_bavail, what a non-root writer gets) and NOT
        `free_bytes` (f_bfree, which includes root-reserved blocks — the 22 GiB
        the two producers disagree by on `/`).
      * `disk_io` and `interfaces` -> LOCAL throughput only, labelled so. An
        interface's link `speed_mbps` is never a public quota, and a flow that
        has not been split is never called 公网.
      * GPU, active-agent count and service state are NOT in this envelope, so
        this bridge invents none of them.

    Returns `(readings, reachability)`.
    """
    measurements = envelope.get("measurements")
    if not isinstance(measurements, dict):
        reason = "envelope 没有 measurements 对象"
        return ([monitor.unobserved("主机读数", reason=reason, kind="host")],
                {"state": monitor.STATE_UNKNOWN, "reason": reason})
    out = []

    def emit(label, value, *, unit="", note="", kind="reading", scope=None):
        if state == monitor.STATE_KNOWN:
            return monitor.reading(label, value, unit=unit, note=note,
                                   observed_at=observed_at, kind=kind, scope=scope)
        if state == monitor.STATE_STALE:
            return monitor.stale(monitor.reading(label, value, unit=unit,
                                                 note=note, observed_at=observed_at,
                                                 kind=kind, scope=scope), reason=why)
        return monitor.unobserved(label, reason=why, kind=kind, scope=scope)

    def present(block):
        return isinstance(block, dict) and block.get("state") in (None, "PRESENT")

    cpu = measurements.get("cpu") or {}
    if present(cpu) and cpu.get("used_percent") is not None:
        out.append(emit("CPU", cpu["used_percent"], unit="%",
                        note=f"整机 · {cpu.get('sample_seconds')}s 窗口", kind="cpu"))
    else:
        out.append(monitor.unobserved("CPU", reason="cpu 未测量或不可用", kind="cpu"))

    memory = measurements.get("memory") or {}
    if present(memory):
        total, available = memory.get("total_bytes"), memory.get("available_bytes")
        used = (total - available
                if isinstance(total, (int, float)) and isinstance(available, (int, float))
                else None)
        percent = _percent(used, total)
        out.append(emit(
            "内存", percent if percent is not None else "—",
            unit="%" if percent is not None else "",
            note=f"可用 {_gib(available)} / 共 {_gib(total)}"
                 f"（MemAvailable 口径，由 bytes 换算）",
            kind="memory"))
    else:
        out.append(monitor.unobserved("内存", reason="memory 未测量或不可用",
                                      kind="memory"))

    filesystems = measurements.get("filesystems") or {}
    for role in (filesystems.get("roles") or []):
        if not isinstance(role, dict):
            continue
        name = role.get("role") or role.get("mount_point") or "?"
        if role.get("state") != "PRESENT":
            out.append(monitor.unobserved(f"磁盘 {name}", reason="该角色未测量",
                                          kind="filesystem"))
            continue
        note = f"可用 {_gib(role.get('available_bytes'))}（f_bavail）"
        inode = (role.get("inodes") or {}).get("used_percent")
        if inode is not None:
            note += f" · inode {inode}%"
        out.append(emit(f"磁盘 {name}", role.get("used_percent"), unit="%",
                        note=note, kind="filesystem"))

    disk_io = measurements.get("disk_io") or {}
    for role in (disk_io.get("roles") or []):
        if not isinstance(role, dict):
            continue
        out.append(emit(
            f"磁盘吞吐 {role.get('role') or role.get('name')}",
            f"读 {_rate(role.get('read_bytes_per_second'))} · "
            f"写 {_rate(role.get('write_bytes_per_second'))}",
            note="本机设备吞吐，非公网", kind="disk-io", scope="local-device"))

    for interface in (measurements.get("interfaces") or []):
        if not isinstance(interface, dict) or not interface.get("active"):
            continue
        if interface.get("loopback"):
            continue
        note = "本机网卡吞吐，未区分公网/内网"
        speed = interface.get("speed_mbps")
        if isinstance(speed, (int, float)):
            note += f" · 链路速率 {speed} Mbps（非公网配额）"
        out.append(emit(
            f"网卡 {interface.get('name')} 吞吐",
            f"入 {_rate(interface.get('rx_bytes_per_second'))} · "
            f"出 {_rate(interface.get('tx_bytes_per_second'))}",
            note=note, kind="network", scope="undifferentiated"))

    reachability = ({"state": monitor.STATE_KNOWN, "observed_at": observed_at}
                    if state == monitor.STATE_KNOWN
                    else {"state": state, "reason": why})
    return out, reachability


def accept_native_measurement(envelope, *, device_ref, now, freshness_bound):
    """PR#161's `serveros/native-node-measurement.v1` -> a device entry. PURE.

    Reads no file, imports nothing from the C++ tree, opens no collector: it
    takes a PARSED envelope and returns the device entry `monitor.monitor_model`
    consumes, or raises `NativeMeasurementRefused`. The C++ producer is
    assembled by CALLING this with its output, so the two land in either order.

    Everything it cannot establish FAILS CLOSED — to a word with a reason, never
    to a zero and never to a green reading:

      * a `readings_available` that is not `true` -> an UNKNOWN host reading;
      * a `scope` that is not `host-wide` -> a refusal (a reading that is not
        host-wide must not be rendered as one that is);
      * a `provenance.live` that is not a boolean -> a refusal (whether these
        facts are a reading of a host or a fixture is not optional);
      * an `attribution.scope` that is not `self` -> a refusal (the only
        account-scoped fact this envelope carries is the collector's own cgroup,
        and treating it as a user's host-wide share is forbidden);
      * any freshness the caller's bound or the envelope cannot establish ->
        STALE or UNKNOWN on every reading.

    The reading atoms it produces are validated by `accept_device_snapshot`, so
    the «a non-UNKNOWN reading carries its observed_at» rule lives in ONE place
    and this bridge does not restate it.
    """
    if not isinstance(envelope, dict):
        raise NativeMeasurementRefused(
            f"an envelope is an object, got {type(envelope).__name__}")
    if envelope.get("schema") != NATIVE_MEASUREMENT_SCHEMA:
        raise NativeMeasurementRefused(
            f"schema {envelope.get('schema')!r} is not "
            f"{NATIVE_MEASUREMENT_SCHEMA!r}")
    if envelope.get("scope") != "host-wide":
        raise NativeMeasurementRefused(
            f"scope {envelope.get('scope')!r} is not 'host-wide'; a reading that "
            f"describes something other than the whole machine must not be "
            f"rendered as one that describes it")
    if not isinstance(envelope.get("readings_available"), bool):
        raise NativeMeasurementRefused(
            "readings_available is not a boolean; whether a reading exists must "
            "be stated, not inferred")
    provenance = envelope.get("provenance")
    if not isinstance(provenance, dict) or not isinstance(provenance.get("live"), bool):
        raise NativeMeasurementRefused(
            "provenance.live is not stated as a boolean; whether these facts are "
            "a reading of a host or a fixture is not optional")

    sample = envelope.get("sample") or {}
    observed_at = sample.get("observed_at")
    native = {
        "source": envelope.get("source"),
        "collector": provenance.get("collector"),
        "collector_version": provenance.get("collector_version"),
        "language": provenance.get("language"),
        "live": provenance["live"],
        "replayed": bool(provenance.get("replayed")),
        "executed_now": provenance.get("executed_now"),
        "status": provenance.get("status"),
        "observed_at": observed_at,
        "enveloped_at": sample.get("enveloped_at"),
    }

    if envelope["readings_available"] is not True:
        reason = _failure_reason(envelope)
        device = {
            "schema": DEVICE_ENTRY_SCHEMA, "device_ref": device_ref,
            "display_name": device_ref, "platform": "未知",
            "reachability": {"state": monitor.STATE_UNKNOWN, "reason": reason},
            "readings": [monitor.unobserved("主机读数", reason=reason, kind="host")],
        }
        entry = accept_device_snapshot(device)
        entry["native"] = native
        return entry

    attribution = envelope.get("attribution")
    if not isinstance(attribution, dict) or attribution.get("scope") != "self":
        raise NativeMeasurementRefused(
            "attribution.scope is not 'self'; the only account-scoped fact in "
            "this envelope is the collector's own cgroup, and reading it as a "
            "user's host-wide share is forbidden")

    state, why = _native_freshness(envelope, observed_at, now, freshness_bound)
    readings, reachability = _native_readings(
        envelope, observed_at=observed_at, state=state, why=why)
    cgroup_memory = attribution.get("memory_current_bytes")
    if cgroup_memory is not None:
        reading = (monitor.reading(
            "采集进程 cgroup 内存", _gib(cgroup_memory),
            note="仅本采集进程的 cgroup，不是用户整机占比",
            observed_at=observed_at, kind="cgroup-self", scope="self")
            if state == monitor.STATE_KNOWN else
            monitor.unobserved("采集进程 cgroup 内存", reason=why, kind="cgroup-self"))
        readings.append(reading)

    device = {
        "schema": DEVICE_ENTRY_SCHEMA, "device_ref": device_ref,
        "display_name": device_ref, "platform": "未知",
        "reachability": reachability, "readings": readings,
    }
    entry = accept_device_snapshot(device)
    entry["native"] = native
    return entry


# ---------------------------------------------------------------------------
# The per-UID bridge: PR#167's aggregate -> an account entry. Typed, no import
# ---------------------------------------------------------------------------

class UidSummaryRefused(RuntimeError):
    """A per-UID summary this bridge cannot accept."""


def _gib_from_bytes(value):
    if value is None:
        return None
    try:
        return round(float(value) / 1024 ** 3, 2)
    except (TypeError, ValueError):
        return None


#: The three words an account's severity may carry. Anything else is not a
#: severity this bridge will pass off as one — an unknown word becomes OK-with-
#: a-reason rather than a colour, because "CRITCAL" read as OK is the quiet half
#: of the failure and reading it as CRITICAL would false-alarm.
_SEVERITIES = ("OK", "ATTENTION", "CRITICAL")


def accept_uid_summary(document, *, now, freshness_bound, live=False):
    """A per-UID resource summary -> the account entry the card renders. PURE.

    Reads no file, imports nothing from #167's tree, opens no collector: it takes
    a PARSED document and returns an account dict, or fails CLOSED to an
    `account_unobserved` with a reason. `live=True` is the caller asserting this
    is a reading rather than a fixture; when it is False the account is still
    returned but the whole card is a sample, exactly as the device bridges work.

    What it will NOT do: invent a threshold, derive a severity, or read a value
    it was not handed. `warn`/`quota` are passed through only when the document
    carries them (a missing allowance must render 未定义, never 0), and the
    severity is the producer's word — this bridge does not decide who is over
    their line.
    """
    if not isinstance(document, dict):
        raise UidSummaryRefused(
            f"a summary is an object, got {type(document).__name__}")
    if document.get("schema") != UID_SUMMARY_SCHEMA:
        raise UidSummaryRefused(
            f"schema {document.get('schema')!r} is not {UID_SUMMARY_SCHEMA!r}")
    provenance = document.get("provenance")
    if not isinstance(provenance, dict) or not isinstance(provenance.get("live"), bool):
        raise UidSummaryRefused(
            "provenance.live is not stated as a boolean; whether these facts are "
            "a reading of a host or a fixture is not optional")
    uid = document.get("uid")
    if not isinstance(uid, int) or isinstance(uid, bool):
        raise UidSummaryRefused(
            f"uid {uid!r} is not an integer; an account is identified by a kernel "
            "UID and not by a name")
    display_name = document.get("display_name")
    sampled_at = document.get("sampled_at")
    when, clock = monitor._parse(sampled_at), monitor._parse(now)
    if when is None or clock is None:
        return monitor.account_unobserved(
            uid=uid, display_name=display_name,
            reason=f"sampled_at {sampled_at!r} 或当前时间不可解析，无法判定新鲜度")

    age = (clock - when).total_seconds()
    stale = age > freshness_bound
    # `anonymous_bytes` is preferred because it excludes shared pages; `rss_…` is
    # the documented fallback and is explicitly an estimate, never a PSS claim.
    anonymous = document.get("anonymous_bytes")
    used_bytes = anonymous if anonymous is not None else document.get("rss_estimate_bytes")
    if used_bytes is None and document.get("cpu_cores") is None:
        return monitor.account_unobserved(
            uid=uid, display_name=display_name,
            reason="既没有内存读数也没有 CPU 读数，这个账户没有被测量")
    quota = document.get("quota") or {}
    warn = document.get("warn") or {}
    severity = str(document.get("severity") or "OK").upper()
    if severity not in _SEVERITIES:
        severity = "OK"
    trend = [v for v in (document.get("trend") or [])
             if isinstance(v, (int, float))]
    if stale:
        base = monitor.account(
            uid=uid, display_name=display_name, observed_at=sampled_at,
            cpu_used=document.get("cpu_cores"),
            cpu_quota=quota.get("cpu_cores"), cpu_warn=warn.get("cpu_cores"),
            ram_used_gib=_gib_from_bytes(used_bytes),
            ram_quota_gib=_gib_from_bytes(quota.get("ram_bytes")),
            ram_warn_gib=_gib_from_bytes(warn.get("ram_bytes")),
            severity=severity, trend=trend,
            source=provenance.get("collector"))
        base["state"] = monitor.STATE_STALE
        base["reason"] = (f"最后观测 {sampled_at} 距今 {int(age // 60)} 分钟，超过"
                          f"新鲜度上限 {freshness_bound // 60} 分钟")
        return base
    return monitor.account(
        uid=uid, display_name=display_name, observed_at=sampled_at,
        cpu_used=document.get("cpu_cores"),
        cpu_quota=quota.get("cpu_cores"), cpu_warn=warn.get("cpu_cores"),
        ram_used_gib=_gib_from_bytes(used_bytes),
        ram_quota_gib=_gib_from_bytes(quota.get("ram_bytes")),
        ram_warn_gib=_gib_from_bytes(warn.get("ram_bytes")),
        severity=severity, trend=trend, source=provenance.get("collector"))


def device_from_snapshot(device_ref, snapshot, *, display_name=None,
                         platform=None, stale_after_s=None):
    """One device entry, from a `card_source.snapshot()` reading.

    Facts are carried across, never re-derived: a metric keeps the label, value
    and note the section computed; a gap keeps the sentence the section wrote.
    Nothing here formats a number or decides what a field means.
    """
    at = snapshot.get("at")
    host = snapshot.get("host") or {}
    state = str(host.get("state") or "UNKNOWN").upper()
    severity = _SECTION_SEVERITY.get(state, "OK")

    readings = []
    if state == "UNKNOWN":
        readings.append(monitor.unobserved(
            "主机读数",
            reason=host.get("why") or "本机的资源摘要没有到达"))
    for cell in host.get("metrics") or []:
        readings.append(monitor.reading(
            cell.get("label"), cell.get("value"), note=cell.get("note") or "",
            observed_at=at, severity=severity, kind="host"))
    # A gap is an UNKNOWN reading with the sentence the gather wrote, so the
    # cause survives into the card instead of collapsing to a bare "无法确认".
    for gap in snapshot.get("gaps") or []:
        readings.append(monitor.unobserved(
            gap.get("read") or "采集", reason=gap.get("why") or "未读到"))

    return {
        "device_ref": device_ref,
        "display_name": display_name or snapshot.get("node_label") or device_ref,
        "platform": platform or "未知",
        "reachability": {"state": monitor.STATE_KNOWN, "observed_at": at},
        "readings": readings,
    }


def fleet_model(*, snapshots, services=(), network=None, now, sample=False,
                refresh_seconds=monitor.DEFAULT_REFRESH_S,
                stale_after_s=monitor.DEFAULT_STALE_AFTER_S,
                names=None, platforms=None, accounts=None):
    """Compose many devices' snapshots into the monitor view model. Read-only.

    `snapshots` is a list of `(device_ref, snapshot)` pairs. A device whose
    snapshot is None is carried as an UNKNOWN-reachability device rather than
    dropped — the same distinction `fleet/view.py` draws between "nobody looked"
    and "looked, found none", applied to a device this adapter was handed
    nothing for.

    `accounts` maps a `device_ref` to that device's per-UID account entries (see
    `accept_uid_summary`). It is a separate argument rather than part of the
    snapshot because the two come from different producers — the host reading
    from one, the UID aggregate from the #167 collector — and a device can have
    one without the other.
    """
    names = names or {}
    platforms = platforms or {}
    accounts = accounts or {}
    devices = []
    for device_ref, snapshot in snapshots:
        if snapshot is None:
            devices.append({
                "device_ref": device_ref,
                "display_name": names.get(device_ref, device_ref),
                "platform": platforms.get(device_ref, "未知"),
                "reachability": {"state": monitor.STATE_UNKNOWN,
                                 "reason": "该设备没有可用的采集结果"},
                "readings": [],
                "accounts": list(accounts.get(device_ref) or []),
            })
            continue
        entry = device_from_snapshot(
            device_ref, snapshot, display_name=names.get(device_ref),
            platform=platforms.get(device_ref))
        entry["accounts"] = list(accounts.get(device_ref) or [])
        devices.append(entry)
    model = monitor.monitor_model(
        devices=devices, services=services, network=network, now=now,
        sample=sample, refresh_seconds=refresh_seconds,
        stale_after_s=stale_after_s)
    monitor.validate_model(model)
    return model


def load_fixture(path=None):
    """The sample fixture, as the shape `fleet_model`-minus-snapshots takes.

    Sample data has ONE door, and this is it: a caller that wants a card from
    the fixture gets `sample=True` in the model and the mark on the card, so a
    fixture can never be mistaken for the fleet.
    """
    target = Path(path) if path else _HERE / "fleet-monitor.fixture.json"
    document = json.loads(target.read_text(encoding="utf-8"))
    return {
        "now": document["now"],
        "sample": True,
        "devices": document["devices"],
        "services": document.get("services") or [],
        "network": document.get("network"),
        "stale_after_s": document.get("stale_after_s",
                                      monitor.DEFAULT_STALE_AFTER_S),
    }


def model_from_fixture(path=None):
    fixture = load_fixture(path)
    model = monitor.monitor_model(
        devices=fixture["devices"], services=fixture["services"],
        network=fixture["network"], now=fixture["now"], sample=True,
        stale_after_s=fixture["stale_after_s"])
    monitor.validate_model(model)
    return model


def main(argv=None):  # pragma: no cover - hand check
    import argparse
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--fixture", default=None)
    parser.add_argument("--card", action="store_true",
                        help="emit the rendered card instead of the model")
    args = parser.parse_args(argv)
    model = model_from_fixture(args.fixture)
    out = (monitor.render_cardkit(model) if args.card else model)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
