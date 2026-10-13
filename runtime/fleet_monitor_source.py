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

#: The schema id the C++ Native Core's `NodeResourceSnapshot v1` must declare to
#: pass the acceptance seam below. This module OWNS the string and the C++ side
#: must match it; nothing is imported from the C++ tree, so the two can land in
#: either order and be assembled by calling `accept_device_snapshot` with the
#: parsed document.
NODE_RESOURCE_SNAPSHOT_SCHEMA = "serveros/node-resource-snapshot.v1"


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
    if schema != NODE_RESOURCE_SNAPSHOT_SCHEMA:
        raise SnapshotRefused(
            f"schema {schema!r} is not {NODE_RESOURCE_SNAPSHOT_SCHEMA!r}; a "
            f"snapshot from an unknown producer version is not accepted")
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
                names=None, platforms=None):
    """Compose many devices' snapshots into the monitor view model. Read-only.

    `snapshots` is a list of `(device_ref, snapshot)` pairs. A device whose
    snapshot is None is carried as an UNKNOWN-reachability device rather than
    dropped — the same distinction `fleet/view.py` draws between "nobody looked"
    and "looked, found none", applied to a device this adapter was handed
    nothing for.
    """
    names = names or {}
    platforms = platforms or {}
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
            })
            continue
        devices.append(device_from_snapshot(
            device_ref, snapshot, display_name=names.get(device_ref),
            platform=platforms.get(device_ref)))
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
