#!/usr/bin/env python3
"""A bounded, typed journal of read-only samples — so a REPORT can say what a
period ACTUALLY looked like instead of drawing a curve over one point.

WHY THIS EXISTS AND WHAT IT REFUSES
-----------------------------------
The REPORT surface is supposed to carry «真实资源时间序列、均/峰/峰值时刻、采样
覆盖率». None of that is derivable from a single reading, and the honest answer for
one sample is not a flat line — it is «coverage 1/1, no trend». So this journal
keeps a small, TYPED record of what was measured, and the summary it produces
always carries its own coverage so a reader can tell «steady» from «nobody looked».

    typed        fixed numeric axes plus `observed_at` and `provenance`; no raw
                 probe payload, no process list, no other account's anything
    bounded      a cap, oldest first; a long-running node does not grow forever
    owner-private 0700 dir, 0600 file, under this user's own state root
    slot-deduped one sample per 10-minute slot, so a replayed tick cannot
                 inflate coverage — it REPLACES its own slot

NO CHART. A trend line needs a real client to have rendered one; `charts_verified`
stays False and the report carries numbers, not a VChart element. A gap is a gap:
it is counted and named, never interpolated, and a missing reading is `None`
rather than `0`.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

SCHEMA = "serveros/snapshot-journal.v1"

#: One sample per ten minutes; the journal's slot key.
SLOT_SECONDS = 600
#: How many samples are kept. A day of ten-minute samples is 144; four days is a
#: comfortable ceiling that still bounds the document.
CAP = 576

#: The typed numeric axes. A reading whose label matches one of these contributes
#: its number; anything else is not carried.
AXES = ("cpu_load1", "mem_percent", "swap_percent", "disk_percent")

_LABEL_AXES = (("负载", "cpu_load1"), ("内存", "mem_percent"),
               ("交换", "swap_percent"), ("磁盘", "disk_percent"))


def journal_path(state_path) -> Path:
    """A sibling of the surfaces document, under this user's own state root."""
    return Path(state_path).parent.parent / "cards" / "samples.json"


def _number(value):
    """The leading number in a formatted reading, or None. Never a guess."""
    text = str(value or "").strip()
    if not text:
        return None
    for index, character in enumerate(text):
        if character.isdigit() or character == ".":
            end = index
            while end < len(text) and (text[end].isdigit() or text[end] == "."):
                end += 1
            try:
                return float(text[index:end])
            except ValueError:
                return None
    return None


def _slot(at) -> int | None:
    try:
        moment = datetime.datetime.fromisoformat(str(at))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        return None
    return int(moment.timestamp()) // SLOT_SECONDS


def sample_from_readings(readings, *, at, provenance) -> dict:
    """One typed sample from `card_source` readings. Missing axes stay None."""
    values = {axis: None for axis in AXES}
    for reading in readings or ():
        if not isinstance(reading, dict) or reading.get("state") != "KNOWN":
            continue
        label = str(reading.get("label") or "")
        for marker, axis in _LABEL_AXES:
            if marker in label and values[axis] is None:
                values[axis] = _number(reading.get("value"))
    return {"at": str(at), "slot": _slot(at), "provenance": str(provenance),
            "readings": values}


class Journal:
    """The bounded sample store. One document, one atomic write."""

    def __init__(self, path) -> None:
        self.path = Path(path)
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"schema": SCHEMA, "samples": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A journal is DERIVED data, not a receipt: an unreadable one is
            # started over rather than refusing the whole surface. Saying so is
            # the point — this is the one document here whose loss is tolerable.
            return {"schema": SCHEMA, "samples": [], "recovered_from_corrupt": True}
        if not isinstance(data, dict) or not isinstance(data.get("samples"), list):
            return {"schema": SCHEMA, "samples": [], "recovered_from_corrupt": True}
        return data

    def _write(self, document: dict) -> None:
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True),
                             encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)
        self._data = document

    def append(self, sample: dict) -> dict:
        """Add one sample, REPLACING its own slot. Bounded, oldest first."""
        slot = sample.get("slot")
        if slot is None:
            raise ValueError("a sample needs a slot; an instant nobody can place "
                             "on a timeline cannot say anything about a period")
        samples = [dict(s) for s in self._data.get("samples") or []]
        samples = [s for s in samples if s.get("slot") != slot]
        samples.append(dict(sample))
        samples = sorted(samples, key=lambda s: (s.get("slot") or 0, s.get("at") or ""))[-CAP:]
        document = {"schema": SCHEMA, "samples": samples}
        self._write(document)
        return {"appended": True, "samples": len(samples)}

    def read(self) -> dict:
        return self._data

    def summary(self, *, start, end) -> dict:
        """Coverage, mean, peak and peak time over [start, end). PURE reading.

        `coverage` is reported as a fraction of the slots the window COULD hold,
        because «3 samples, all fine» and «3 samples out of 144, the rest
        unread» are different claims and only one of them is about the machine.
        """
        begin, finish = _slot(start), _slot(end)
        if begin is None or finish is None or finish <= begin:
            return {"samples": 0, "expected": 0, "coverage": None,
                    "trend": "UNKNOWN", "axes": {}, "gaps": None,
                    "says": "窗口读不出来，不做时段统计"}
        rows = [s for s in (self._data.get("samples") or [])
                if isinstance(s, dict) and isinstance(s.get("slot"), int)
                and begin <= s["slot"] < finish]
        slots = [s["slot"] for s in rows]
        expected = max(0, (finish - begin))
        axes = {}
        for axis in AXES:
            points = [(s["slot"], (s.get("readings") or {}).get(axis))
                      for s in rows if (s.get("readings") or {}).get(axis) is not None]
            if not points:
                axes[axis] = {"n": 0, "mean": None, "peak": None, "peak_at": None}
                continue
            values = [v for _, v in points]
            peak_slot, peak = max(points, key=lambda pair: pair[1])
            axes[axis] = {
                "n": len(values),
                "mean": round(sum(values) / len(values), 3),
                "peak": peak, "peak_at": _slot_time(peak_slot),
            }
        gaps = expected - len(set(slots))
        return {"samples": len(rows), "expected": expected,
                "coverage": (round(len(set(slots)) / expected, 3) if expected else None),
                "axes": axes, "gaps": gaps,
                "trend": ("UNKNOWN" if len(set(slots)) < 2 else "OK"),
                "says": (f"本时段 {len(set(slots))}/{expected} 个采样点"
                         + ("（不足以评价趋势）" if len(set(slots)) < 2 else "")
                         + (f"，缺口 {gaps}" if gaps else ""))}


def _slot_time(slot) -> str | None:
    if not isinstance(slot, int):
        return None
    moment = datetime.datetime.fromtimestamp(slot * SLOT_SECONDS,
                                             tz=datetime.timezone.utc)
    return moment.astimezone().strftime("%H:%M")
