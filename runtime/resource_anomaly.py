#!/usr/bin/env python3
"""§18 · Resource anomalies — the sixteen codes this layer grades.

Where this sits
---------------
`anomaly.py` detects what is wrong with PROCESSES and RUNTIME GROUPS: a busy
group, an orphaned tree, a task that went idle while its runtime kept running.
That module owns the process layer and its vocabulary.

This module is the RESOURCE layer §18 describes: disks, memory, swap, the
Docker store, containers, build cache and tunnels. It reuses `anomaly.signal`
and the same contract file, so a signal from here has the same shape, the same
`measured`-carries-its-threshold rule and the same severity table as a signal
from there. It is a second producer over the same model, not a second model.

Every code, and what would have to be measured to emit it
--------------------------------------------------------
§18 names fifteen codes and requires the system to be able to REPRESENT them.
This module grades those fifteen plus one more, `MEMORY_PRESSURE_ACTIVE`, which
§18 does not name and which exists because the fifteen it does name cannot
express the difference between a full machine and a stalled one — see the note
above `_memory_signals`. Representing is not the same as emitting, and this
module keeps the two apart in the payload: `codes_emitted` and
`codes_not_evaluated`. A code whose input was not supplied, or was supplied as
`UNAVAILABLE`, appears in the second list with the missing input named —
because the alternative, an absent code being read as "no problem here", is the
exact failure §27 forbids one layer down.

    DISK_WARNING / DISK_CRITICAL   filesystem used_percent, two declared tiers
    INODE_CRITICAL                 inode used_percent on the same filesystems
    MEMORY_PRESSURE                host memory used against total — a WATERMARK
    SWAP_PRESSURE                  swap used against total — a WATERMARK, with
                                   the memory PSI and the paging rate beside it
    MEMORY_PRESSURE_ACTIVE         memory PSI: how much work stalled. The
                                   PRESSURE half of the two above
    DOCKER_STORAGE_HIGH            the Docker data root's own filesystem, or
                                   `docker system df`'s total store size
    RUNTIME_UNOWNED                `hygiene.py`'s UNKNOWN_OWNER rows
    RUNTIME_ORPHANED               `hygiene.py`'s ORPHANED rows
    DESIRED_ACTUAL_MISMATCH        `desired.py`'s mismatch rows, reused as-is
    PROTECTED_RESOURCE_AT_RISK     a protected resource standing on a
                                   filesystem that is itself in a warning tier
    CONTAINER_LOG_LARGE            per-container log file size
    CONTAINER_WRITABLE_LAYER_LARGE per-container writable layer, from
                                   `docker ps -a --size`
    BUILD_CACHE_HIGH               the BuildKit cache size
    TUNNEL_UNHEALTHY               a declared tunnel whose health verdict is
                                   not healthy
    TUNNEL_OWNER_MISSING           a tunnel row with no owner of record

Observation only, in the strongest available sense
--------------------------------------------------
Nothing here executes a verb. There is no `docker` call in this file at all:
every input is a document another producer already measured, which is §28's
rule (`producer → normalized Observation → service → consumer`) applied to
anomalies. The output is a signal list. §25 forbids `high CPU => kill` and
`high memory => kill`, and the way that is enforced here is that there is no
function to call even if a caller wanted to: this module returns dicts.

The two disk tiers exist because one number cannot say both "plan for this"
and "stop creating things here". §31's verdict is a four-valued one
(`NO ACTION / MONITOR / GOVERNANCE REQUIRED / CRITICAL`) and it needs at least
two disk tiers to produce it.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


anomaly = _load("serveros_runtime_anomaly", "anomaly.py")
graph = _load("serveros_runtime_graph", "graph.py")

FACT = anomaly.FACT
INFERRED = anomaly.INFERRED
UNKNOWN = anomaly.UNKNOWN

CONTRACT = _HERE / "attribution-contract.yaml"

# The sixteen, §18's fifteen in §18's order followed by the one this layer
# added. A test asserts the contract's `resource_anomalies:` block names exactly
# this set: a code that exists in code and not in the contract is a severity
# nobody reviews, and a code in the contract and not in code is the
# declared-but-absent pattern.
CODES = (
    "DISK_WARNING",
    "DISK_CRITICAL",
    "INODE_CRITICAL",
    "MEMORY_PRESSURE",
    "SWAP_PRESSURE",
    "MEMORY_PRESSURE_ACTIVE",
    "DOCKER_STORAGE_HIGH",
    "RUNTIME_UNOWNED",
    "RUNTIME_ORPHANED",
    "DESIRED_ACTUAL_MISMATCH",
    "PROTECTED_RESOURCE_AT_RISK",
    "CONTAINER_LOG_LARGE",
    "CONTAINER_WRITABLE_LAYER_LARGE",
    "BUILD_CACHE_HIGH",
    "TUNNEL_UNHEALTHY",
    "TUNNEL_OWNER_MISSING",
)

# Severity per code. Transcribed from the contract and asserted equal to it, by
# the same rule `anomaly.SEVERITY` follows. The ranking is about how fast a
# person should look, not about what may be done: nothing in this round may be
# done at all.
SEVERITY = {
    # Two tiers of the same measurement. WARNING is "this is going to matter";
    # CRITICAL is "do not put anything else here". Collapsing them would make
    # the §31 verdict a coin flip.
    "DISK_WARNING": "medium",
    "DISK_CRITICAL": "high",
    "INODE_CRITICAL": "high",
    "MEMORY_PRESSURE": "medium",
    "SWAP_PRESSURE": "medium",
    # Medium like the two watermarks above, and not higher. Severity here is
    # about how fast a person should look, and `refuse` already carries the one
    # consequence this system is permitted to take; a second axis saying the
    # same thing louder is how the two start to disagree.
    "MEMORY_PRESSURE_ACTIVE": "medium",
    "DOCKER_STORAGE_HIGH": "medium",
    # Low, and deliberately: on a shared host most containers are unowned and
    # that is a statement about declarations, not about the containers. A
    # medium here would put thirty rows in front of a person every tick and
    # they would stop reading the list.
    "RUNTIME_UNOWNED": "low",
    "RUNTIME_ORPHANED": "medium",
    "DESIRED_ACTUAL_MISMATCH": "medium",
    # High, and the only code here whose subject is a PERMISSION rather than a
    # pressure. A protected resource that is at risk fails silently: the
    # protection prevents the automatic path from acting, and nothing else was
    # watching the thing the protection was for.
    "PROTECTED_RESOURCE_AT_RISK": "high",
    "CONTAINER_LOG_LARGE": "low",
    "CONTAINER_WRITABLE_LAYER_LARGE": "low",
    "BUILD_CACHE_HIGH": "medium",
    "TUNNEL_UNHEALTHY": "high",
    "TUNNEL_OWNER_MISSING": "medium",
}

# Threshold keys this module reads, so a reader can see which policy inputs
# feed the resource layer without reading the body.
THRESHOLD_KEYS = (
    "filesystem_percent_high",
    "filesystem_percent_critical",
    "inode_percent_high",
    "memory_percent_high",
    "swap_percent_high",
    # The pair that says whether the swap watermark above is pressure now or a
    # high-water mark from some earlier moment. Listed here so a caller that
    # supplies its own `thresholds` gets them filled in and the card can print
    # what the stall figure was compared against.
    "psi_memory_some_avg60_percent_high",
    "psi_memory_full_avg60_percent_high",
    "docker_storage_percent_high",
    "docker_build_cache_bytes_high",
    "container_writable_layer_bytes_high",
    "container_log_bytes_high",
)

# Verdicts from the tunnel health model that mean "this is not working". Kept
# as a tuple rather than a single equality because the health model's states
# are the contract's, and matching one spelling is how a rule stops applying
# the first time the contract grows a synonym.
TUNNEL_BAD_STATES = ("FAILED", "DEGRADED", "BROKEN", "UNHEALTHY", "DOWN")


def _percent(value):
    """A percentage as a float, from either a number or `df`'s `"85%"`."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().rstrip("%")
    try:
        return float(text)
    except ValueError:
        return None


def _unavailable(document) -> bool:
    """A document that names a failure, or that nobody supplied.

    `inventory.py` answers an unreachable daemon with `{"state": UNAVAILABLE,
    "reason": ...}` rather than an empty list, and §27 makes that an invariant
    with a test. This module must not defeat it: an unreadable Docker store is
    not an empty one, and the caller has to be able to see the difference in
    `codes_not_evaluated`.

    `None` is included, and it is the case this was found by: the caller that
    did not ask for `docker system df` supplies no `space` key at all, and a
    check that only recognized a failure DICT read that as a successful
    measurement of zero — so DOCKER_STORAGE_HIGH and BUILD_CACHE_HIGH came back
    in `codes_graded` on a call where nothing had looked at either. `None` and
    `{"state": UNAVAILABLE}` are both "no measurement here"; only one of them
    used to be spelled that way.
    """
    if document is None:
        return True
    return isinstance(document, dict) and document.get("state") in (
        UNKNOWN, "UNAVAILABLE", "PROVIDER_ERROR", "ABSENT")


class _Gathered:
    """Which inputs arrived, so `codes_not_evaluated` can name the gap."""

    def __init__(self) -> None:
        self.have: set[str] = set()
        self.missing: dict[str, str] = {}

    def take(self, name: str, document, *, why: str) -> bool:
        # An empty mapping counts as not-supplied. `detect` normalizes every
        # absent argument to `{}` so the body can index it without guarding,
        # and without this the two would be indistinguishable: an empty
        # hygiene report would read as "a report that found nothing" rather
        # than "a report nobody made".
        if document is None or document == {}:
            self.missing[name] = f"{why} was not supplied to this detector"
            return False
        if _unavailable(document):
            reason = (document or {}).get("reason") if isinstance(document, dict) else None
            self.missing[name] = (
                f"{why} is {reason}" if reason else f"{why} is UNAVAILABLE")
            return False
        self.have.add(name)
        return True


def _signal(code: str, subject: str, measured: dict, *, window: str,
            why: str, source: str, observed_at, freshness=None) -> dict:
    """One §18 anomaly, under §26's provenance rules.

    A thin wrapper over `anomaly.signal`, and it exists rather than being
    inlined so that the two fields §26 adds — `source` and `freshness` — cannot
    be forgotten on one branch of this module and remembered on another.
    """
    row = anomaly.signal(code, subject, measured, window=window, why=why)
    row["severity"] = SEVERITY[code]
    row["source"] = source
    row["observed_at"] = observed_at
    row["freshness"] = freshness or graph.freshness_of(observed_at, now=observed_at,
                                                      source=source)
    return row


# Moved to `anomaly.margin_key`, and bound here rather than copied. Both
# detectors grade codes and both need the same derivation, and this module
# already loads `anomaly` — the other direction would make the two load each
# other. A second copy would be the file that falls behind the day a third
# spelling of a threshold key appears; the `_high_bytes` suffix was added to
# the real one and would have been missing from the copy.
margin_key = anomaly.margin_key


def _level(code: str, subject: str, value, threshold) -> dict:
    """What a graded measurement IS, reported whether or not it fired.

    A signal is only emitted ABOVE the threshold, so the signal list cannot
    answer "has it come back down far enough to close the alert about it": at
    84.9% the DISK_WARNING list is empty, and empty is also what a host with no
    readable disks produces. The alert layer's hysteresis is therefore a
    comparison against this row rather than an inference from an absence —
    which is the same discipline `codes_graded` vs `codes_not_evaluated`
    applies one level up: "below the line" and "not measured" are different
    answers and must not be spelled the same way.
    """
    return {
        "code": code,
        "subject": subject,
        "value": value,
        "threshold": threshold,
        "margin_key": margin_key(_THRESHOLDS.get(code)),
    }


# ---------------------------------------------------------------------------
# One function per group of codes. Each returns (signals, note_or_None), so a
# branch that found nothing can say what it did not find rather than returning
# an empty list that reads the same as a measurement of zero.
# ---------------------------------------------------------------------------

def _filesystem_rows(disk: dict) -> list[dict]:
    """The filesystems to grade, from both halves of `disk_status()`.

    `roles` are the ones policy reads (ROOT_FS, DATA_FS, DOCKER_FS, HOME) and
    `others` are everything else real. They carry the same numbers under
    different key names — `roles` uses `inodes.used_percent` from `statvfs`,
    `others` uses `df`'s `inodes_percent` string — and normalizing here rather
    than at each call site is what keeps the two from drifting apart.
    """
    rows: dict[str, dict] = {}

    def _add(subject: str, *, role, mount, used, inodes, free) -> None:
        # Deduplicated by the FILESYSTEM, not by the role. On this host
        # ROOT_FS and DOCKER_FS both land on `/`, so a per-role loop grades
        # one disk twice and prints two CRITICAL rows for one problem — which
        # reads as two problems. `disk_status()` already warns about this one
        # layer down; the merge here is that warning applied rather than
        # repeated.
        row = rows.setdefault(subject, {
            "subject": subject, "roles": [], "mount": mount,
            "used_percent": used, "inode_percent": inodes,
            "free_bytes": free, "shared_with": None,
        })
        if role and role not in row["roles"]:
            row["roles"].append(role)
        # The first non-null reading wins. Two roles reading the same mount
        # get their numbers from the same `statvfs` call, so this is only a
        # tie-break for the case where one of them was unread.
        if row["used_percent"] is None:
            row["used_percent"] = used
        if row["inode_percent"] is None:
            row["inode_percent"] = inodes

    for mount in disk.get("roles") or []:
        if not isinstance(mount, dict) or mount.get("state") != "PRESENT":
            continue
        inodes = mount.get("inodes") or {}
        target = mount.get("mount_point") or mount.get("path")
        _add(f"filesystem:{target}", role=mount.get("role"), mount=target,
             used=_percent(mount.get("used_percent")),
             inodes=_percent(inodes.get("used_percent")),
             free=mount.get("free_bytes"))
        if mount.get("shared_with"):
            rows[f"filesystem:{target}"]["shared_with"] = mount["shared_with"]
    for other in disk.get("others") or []:
        if not isinstance(other, dict) or "target" not in other:
            continue
        _add(f"filesystem:{other.get('target')}", role=None,
             mount=other.get("target"),
             used=_percent(other.get("used_percent")),
             inodes=_percent(other.get("inodes_percent")),
             free=other.get("avail_bytes"))
    return list(rows.values())


def _disk_signals(disk: dict, thresholds: dict, *, observed_at) -> tuple[list, list, str | None]:
    """DISK_WARNING, DISK_CRITICAL, INODE_CRITICAL.

    Returns `(signals, levels, note)`. `levels` is what every graded filesystem
    is AT, reported whether or not it crossed a tier — see `_level()` for why
    the below-threshold reading is not the same thing as no reading.
    """
    warnings, critical, levels = [], [], []
    warning_at = thresholds["filesystem_percent_high"]
    critical_at = thresholds["filesystem_percent_critical"]
    inode_at = thresholds["inode_percent_high"]
    ungraded = 0
    for row in _filesystem_rows(disk):
        used, inodes = row["used_percent"], row["inode_percent"]
        if used is None and inodes is None:
            ungraded += 1
            continue
        if used is not None:
            # Two tiers of one number, so two levels from one reading. Both are
            # emitted before either comparison, because which one fires is a
            # property of the threshold and not of the measurement.
            levels.append(_level("DISK_WARNING", row["subject"], used, warning_at))
            levels.append(_level("DISK_CRITICAL", row["subject"], used, critical_at))
        if used is not None and used >= critical_at:
            critical.append(_signal(
                "DISK_CRITICAL", row["subject"],
                {"used_percent": used, "threshold_percent": critical_at,
                 "warning_percent": warning_at, "roles": row["roles"],
                 "free_bytes": row["free_bytes"],
                 "shared_with": row["shared_with"]},
                window="point in time", source="host-filesystem",
                observed_at=observed_at,
                why=("this filesystem is at or above the critical tier. It is "
                     "not a cleanup instruction — it is the signal §25 permits "
                     "to refuse the CREATION of a new disposable resource here"),
            ))
        elif used is not None and used >= warning_at:
            warnings.append(_signal(
                "DISK_WARNING", row["subject"],
                {"used_percent": used, "threshold_percent": warning_at,
                 "critical_percent": critical_at, "roles": row["roles"],
                 "free_bytes": row["free_bytes"],
                 "shared_with": row["shared_with"]},
                window="point in time", source="host-filesystem",
                observed_at=observed_at,
                why="this filesystem is at or above the warning tier",
            ))
        if inodes is not None:
            levels.append(_level("INODE_CRITICAL", row["subject"], inodes, inode_at))
        if inodes is not None and inodes >= inode_at:
            warnings.append(_signal(
                "INODE_CRITICAL", row["subject"],
                {"inode_percent": inodes, "threshold_percent": inode_at,
                 "roles": row["roles"]},
                window="point in time", source="host-filesystem",
                observed_at=observed_at,
                why=("no free inodes. A filesystem with free bytes and no free "
                     "inodes cannot create a file, so this is a separate gate "
                     "with a separate threshold rather than a second reading "
                     "of the byte one"),
            ))
    note = None
    if ungraded:
        note = (f"{ungraded} filesystem(s) were reported without a usage "
                f"figure and are not graded — unread is not empty")
    return critical + warnings, levels, note


def _memory_signals(host: dict, thresholds: dict, *, observed_at) -> tuple[list, list, dict]:
    """MEMORY_PRESSURE, SWAP_PRESSURE, MEMORY_PRESSURE_ACTIVE.

    Three codes over two axes, and the split between them is the point. A
    WATERMARK answers "how full is it". PRESSURE answers "how much work is
    stalling". Both are real measurements, neither answers the other's
    question, and this host has been both machines:

      * 2026-09-15 — 99.2% of swap in use, memory PSI 0.00 across every
        window, `pswpout` unmoved over forty minutes. Full, and idle.
      * 2026-09-29 — swap at 80.5%, BELOW its 90% threshold so the watermark
        code stayed silent, while memory PSI some avg60 ran 6.80 → 10.53
        against a threshold of 1.0 and `pswpout` reached 53,238 pages/second.
        Room to spare, and stalling — and the monitoring was green throughout.

    The second machine is why this was not a renaming. Grading a watermark and
    reporting it under a name that claims pressure does not only mislabel the
    first machine; it leaves the second one unreported, because the measurement
    that would have caught it was never read at all.

    Returns `(signals, levels, gaps)`, with `gaps` keyed BY CODE — the shape
    `_docker_signals` returns. One string for the whole group would mean a host
    whose PSI could not be read reported the two watermarks as unmeasured as
    well, and "nobody looked at your swap watermark" is a different sentence
    from "nobody looked at the stall figures". §27, one level down.
    """
    signals: list[dict] = []
    levels: list[dict] = []
    gaps: dict[str, str] = {}
    memory = host.get("memory") or {}
    total = memory.get("total_bytes")
    used = memory.get("used_bytes")
    mem_at = thresholds["memory_percent_high"]
    if total and used is not None:
        percent = round(100.0 * used / total, 1)
        levels.append(_level("MEMORY_PRESSURE", "host:memory", percent, mem_at))
        if percent >= mem_at:
            signals.append(_signal(
                "MEMORY_PRESSURE", "host:memory",
                {"used_percent": percent, "threshold_percent": mem_at,
                 "used_bytes": used, "total_bytes": total,
                 "available_bytes": memory.get("available_bytes")},
                window="point in time", source="host-memory",
                observed_at=observed_at,
                why=("resident memory is at or above the declared threshold. "
                     "§25 forbids acting on this: the permitted response is to "
                     "refuse a NEW allocation, never to stop one that exists"),
            ))
    # SWAP_PRESSURE IS A WATERMARK, AND IS NOW NAMED AND GRADED AS ONE.
    #
    # It was called pressure while being graded on the watermark alone, and the
    # watermark was the only reading it had: this module is handed documents
    # and holds no inventory, and the summary it was handed carried no PSI.
    # The READING was repaired last round — `memory_pressure` and the two
    # thresholds travel with the signal and the card prints the stall figures
    # beside the watermark. What is repaired here is the CLAIM, which a better
    # reading on its own could not fix.
    #
    # So: same measurement, same threshold, same identity, narrower name, and
    # `advisory` instead of `refuse`. That last one is graded on what the code
    # MEASURES rather than inherited from what its predecessor measured, which
    # is why `MEMORY_PRESSURE` keeps `refuse` while this does not: MemAvailable
    # is the kernel's own estimate with reclaimable cache already subtracted, a
    # low figure is memory the machine genuinely cannot get back, whereas a
    # swap high-water mark only rises. Pages are written to swap when memory
    # was short and read back only when something touches them again, so the
    # 99.2%-and-idle machine above and a thrashing one report the same
    # watermark — and only one of them is short of memory.
    #
    # The reading travels in the provider's own shape (`inventory.
    # pressure_stall()`'s per-resource dict, `UNAVAILABLE` included) rather
    # than pre-decided into a verdict: whether a stall figure is a problem is
    # the same question `_swap_note` asks of these bytes, and answering it
    # twice in two places is how the two answers start to disagree.
    swap = memory.get("swap") or {}
    swap_total, swap_used = swap.get("total_bytes"), swap.get("used_bytes")
    swap_at = thresholds["swap_percent_high"]
    swap_percent = None
    if swap_total and swap_used is not None:
        swap_percent = round(100.0 * swap_used / swap_total, 1)
        levels.append(_level("SWAP_PRESSURE", "host:swap", swap_percent, swap_at))
        if swap_percent >= swap_at:
            signals.append(_signal(
                "SWAP_PRESSURE", "host:swap",
                {"used_percent": swap_percent, "threshold_percent": swap_at,
                 "used_bytes": swap_used, "total_bytes": swap_total,
                 # Absent from a summary the caller built itself, in which case
                 # the card says the pressure was not established rather than
                 # implying the watermark settled it.
                 "memory_pressure": (host.get("pressure") or {}).get("memory"),
                 # The third axis, and the one that decides what a high
                 # watermark MEANS. `_swap_note` on the advisory path has
                 # separated "full and quiet" from "full and thrashing" since it
                 # was written, but it reads the live inventory; this signal
                 # reaches the register and the card, and a watermark there
                 # without an activity figure beside it is the single number
                 # that cannot answer its own question. `None` when the caller
                 # built its own summary rather than asking the inventory.
                 "paging": host.get("paging"),
                 "psi_memory_some_avg60_percent_high":
                     thresholds.get("psi_memory_some_avg60_percent_high"),
                 "psi_memory_full_avg60_percent_high":
                     thresholds.get("psi_memory_full_avg60_percent_high")},
                window="point in time", source="host-memory",
                observed_at=observed_at,
                why=("swap in use is at or above the declared threshold. This "
                     "is a watermark and not a measurement of pressure: pages "
                     "are written to swap when memory was short and read back "
                     "only when something touches them again, so a swap file "
                     "that filled once stays full while nothing is paging. It "
                     "is reported beside the stall figures below for that "
                     "reason, and it is advisory — a watermark is not grounds "
                     "for refusing the creation of anything"),
            ))
    # MEMORY_PRESSURE_ACTIVE — the pressure half, and the one measurement that
    # made the split necessary.
    #
    # Graded on `some` rather than `full`, and deliberately. `some` is "at least
    # one task was stalled"; `full` is "every non-idle task was". This is a
    # shared multi-tenant host, where one account's runtime stalling while
    # every other account is fine is exactly the thing worth knowing about, and
    # `full` would report that machine as quiet. The two coinciding (3.16 and
    # 3.15 on this host, 2026-09-29) is a whole-machine stall, which `some`
    # catches too — the choice only changes the answer on the machine where the
    # stall is partial, and there it is the difference between noticing and
    # not. `full` is carried in `measured` beside it so a reader can see both.
    #
    # §4 binds `why`: this says what was measured and not what produced it.
    # The 2026-09-29 reading is the reason — 188 GiB available and a 7.8% full
    # stall at the same moment is a combination nobody has explained, and a
    # sentence naming a cause would have been a guess wearing a measurement's
    # clothes.
    pressure = (host.get("pressure") or {}).get("memory")
    paging = host.get("paging")
    psi_at = thresholds["psi_memory_some_avg60_percent_high"]
    some = pressure.get("some") if isinstance(pressure, dict) else None
    some_avg60 = some.get("avg60") if isinstance(some, dict) else None
    if _unavailable(pressure) or some_avg60 is None:
        # "Cannot tell" is not "fine". `/proc/pressure` is absent on a pre-4.20
        # kernel and under `psi=0`, and an unreadable stall figure read as no
        # stall is the conflation this code exists to prevent — it would not
        # become true by being made in a gap instead of a field.
        gaps["MEMORY_PRESSURE_ACTIVE"] = (
            "the memory pressure-stall figures were not readable"
            + (f": {pressure.get('reason')}"
               if isinstance(pressure, dict) and pressure.get("reason") else "")
            + "; a machine nobody measured for stalls is not a machine with none")
    else:
        available = memory.get("available_bytes")
        levels.append(_level("MEMORY_PRESSURE_ACTIVE", "host:memory-pressure",
                             some_avg60, psi_at))
        if some_avg60 >= psi_at:
            signals.append(_signal(
                "MEMORY_PRESSURE_ACTIVE", "host:memory-pressure",
                {"memory_pressure": pressure,
                 "psi_memory_some_avg60_percent_high": psi_at,
                 "psi_memory_full_avg60_percent_high":
                     thresholds.get("psi_memory_full_avg60_percent_high"),
                 # The watermark beside the pressure, for the same reason the
                 # watermark carries the pressure: neither number means what it
                 # looks like without the other. A reader can see that the
                 # machine was stalling AND that it had memory to spare, which
                 # is a fact about the stall and not about the memory.
                 "available_bytes": available,
                 "available_percent": (round(100.0 * available / total, 1)
                                       if total and available is not None else None),
                 "swap_used_percent": swap_percent,
                 # Activity, not a verdict, and `None` when the caller built
                 # its own summary rather than asking the inventory.
                 "paging": paging},
                window="point in time", source="host-memory",
                observed_at=observed_at,
                why=("tasks on this host are stalling on memory: they are "
                     "waiting on reclaim or on swap while work they could be "
                     "doing is held up. This is a measurement of how much work "
                     "waited, which is a different fact from how full the "
                     "machine is — the figures beside it say both, and §25 "
                     "permits one response to it, refusing a NEW allocation"),
            ))
    if total is None:
        gaps["MEMORY_PRESSURE"] = "the host memory summary carried no usable totals"
    if total is None or (swap_total is not None and swap_used is None):
        gaps["SWAP_PRESSURE"] = "the host memory summary carried no usable totals"
    return signals, levels, gaps


def _docker_signals(docker: dict, thresholds: dict, *, observed_at) -> tuple[list, list, dict]:
    """DOCKER_STORAGE_HIGH, BUILD_CACHE_HIGH, the two container-size codes.

    Returns `(signals, levels, not_evaluated)` where the last element maps a
    code to the reason it could not be graded. Only the codes measured as a
    reading are levelled; the two that report their own `size_bytes` are too,
    with no margin key — see `margin_key()` for what that absence means.
    """
    signals: list[dict] = []
    levels: list[dict] = []
    gaps: dict[str, str] = {}
    storage_at = thresholds["docker_storage_percent_high"]
    cache_at = thresholds["docker_build_cache_bytes_high"]
    layer_at = thresholds["container_writable_layer_bytes_high"]
    log_at = thresholds["container_log_bytes_high"]

    space = docker.get("space")
    if _unavailable(space):
        reason = (space or {}).get("reason") if isinstance(space, dict) else None
        gaps["DOCKER_STORAGE_HIGH"] = (
            f"`docker system df`: {reason or 'not collected'}. On this host it "
            f"costs ~22s and is opt-in by name — and not asking is not the "
            f"same as finding 0")
        gaps["BUILD_CACHE_HIGH"] = (
            f"the BuildKit cache size comes from `docker system df`, which "
            f"reported {reason or 'nothing'}")
    else:
        sections = (space or {}).get("sections") or {}
        total = sum((v.get("size_bytes") or 0) for v in sections.values()
                    if isinstance(v, dict))
        if total:
            # The store size is compared against the host's own total as a
            # percentage, because an absolute byte threshold for "Docker is
            # too big" cannot be written without knowing the disk.
            host_total = docker.get("data_root_total_bytes")
            percent = (round(100.0 * total / host_total, 1)
                       if host_total else None)
            if percent is not None:
                levels.append(_level("DOCKER_STORAGE_HIGH", "docker:store",
                                     percent, storage_at))
            if percent is not None and percent >= storage_at:
                signals.append(_signal(
                    "DOCKER_STORAGE_HIGH", "docker:store",
                    {"used_percent": percent, "threshold_percent": storage_at,
                     "store_bytes": total, "filesystem_bytes": host_total,
                     "sections": {k: v.get("size_bytes") for k, v in sections.items()}},
                    window="point in time", source="docker-space",
                    observed_at=observed_at,
                    why="the Docker store is at or above its declared share of the filesystem",
                ))
        build_cache = sections.get("build_cache") if sections else None
        cache_bytes = (build_cache or {}).get("size_bytes")
        if cache_bytes is not None:
            levels.append(_level("BUILD_CACHE_HIGH", "docker:build-cache",
                                 cache_bytes, cache_at))
        if cache_bytes is not None and cache_bytes >= cache_at:
            signals.append(_signal(
                "BUILD_CACHE_HIGH", "docker:build-cache",
                {"size_bytes": cache_bytes, "threshold_bytes": cache_at,
                 "reclaimable_bytes": (build_cache or {}).get("reclaimable_bytes")},
                window="point in time", source="docker-space",
                observed_at=observed_at,
                why=("the BuildKit cache is at or above the declared threshold. "
                     "On this host two buildx instances hold tens of GB and are "
                     "BUILD INFRASTRUCTURE, not litter — `reclaimable_bytes` is "
                     "what a prune WOULD free, not what is safe to free"),
            ))

    sizes = docker.get("sizes")
    if _unavailable(sizes):
        gaps["CONTAINER_WRITABLE_LAYER_LARGE"] = (
            "`docker ps -a --size` was not collected")
    else:
        for row in (sizes or {}).get("containers") or []:
            layer = row.get("size_rw_bytes")
            if layer is not None:
                levels.append(_level("CONTAINER_WRITABLE_LAYER_LARGE",
                                     f"container:{row.get('name')}", layer, layer_at))
            if layer is not None and layer >= layer_at:
                signals.append(_signal(
                    "CONTAINER_WRITABLE_LAYER_LARGE", f"container:{row.get('name')}",
                    {"size_rw_bytes": layer, "threshold_bytes": layer_at,
                     "size_root_fs_bytes": row.get("size_root_fs_bytes"),
                     "state": row.get("state"),
                     "note": ("the daemon's own rounded figure — SI units to "
                              "three significant digits, so this is a "
                              "classification and not an exact byte count")},
                    window="point in time", source="docker-sizes",
                    observed_at=observed_at,
                    why="the container's writable layer is at or above the declared threshold",
                ))

    logs = docker.get("logs")
    if _unavailable(logs):
        gaps["CONTAINER_LOG_LARGE"] = (
            (logs or {}).get("reason")
            or "the per-container log sizes were not collected")
    else:
        graded = 0
        for row in (logs or {}).get("containers") or []:
            size = row.get("log_bytes")
            if size is None:
                continue
            graded += 1
            levels.append(_level("CONTAINER_LOG_LARGE",
                                 f"container:{row.get('name')}", size, log_at))
            if size >= log_at:
                signals.append(_signal(
                    "CONTAINER_LOG_LARGE", f"container:{row.get('name')}",
                    {"log_bytes": size, "threshold_bytes": log_at,
                     "driver": row.get("driver")},
                    window="point in time", source="docker-logs",
                    observed_at=observed_at,
                    why="the container's log file is at or above the declared threshold",
                ))
        if not graded:
            # Every container reported a size of `None`. That is the honest
            # answer on a host whose data root the observing user cannot read,
            # and it must not be reported as "all logs are small".
            gaps["CONTAINER_LOG_LARGE"] = (
                "no container log size could be measured — "
                f"{(logs or {}).get('unobservable') or 'all'} of "
                f"{(logs or {}).get('count') or 0} logs were unreadable, which "
                "is unknown rather than small")
    return signals, levels, gaps


def _hygiene_signals(hygiene: dict, *, observed_at) -> tuple[list[dict], dict]:
    """RUNTIME_UNOWNED, RUNTIME_ORPHANED, from `hygiene.py`'s own classes.

    Reused rather than recomputed: §18 says to reuse the existing hygiene
    model, and the way to reuse it is to read its classification instead of
    asking `docker ps` a second question with a second rule attached.

    An unreachable Docker is a GAP and not an empty classification. `Hygiene`
    answers that case with `{"docker": {"state": UNAVAILABLE, "reason": ...}}`
    — correct, and its own report says so in as many words — but the two codes
    below read `docker["containers"]`, and a missing key there iterates over
    nothing. The result was two codes in `codes_graded` with zero signals,
    which is the sentence §27 exists to forbid: this is where "docker
    observation unavailable" would have been served as "0 containers, all
    owned".
    """
    signals: list[dict] = []
    docker = hygiene.get("docker") or {}
    if _unavailable(docker):
        reason = (docker or {}).get("reason")
        why = (f"the container hygiene report could not classify anything: "
               f"{reason or 'Docker was unreachable'}. No container was "
               f"examined, which is not the same as none being unowned")
        return signals, {"RUNTIME_UNOWNED": why, "RUNTIME_ORPHANED": why}
    rows = docker.get("containers") or []
    for row in rows:
        name = row.get("name")
        classification = row.get("classification")
        if classification == "UNKNOWN_OWNER":
            signals.append(_signal(
                "RUNTIME_UNOWNED", f"container:{name}",
                {"classification": classification, "running": row.get("running"),
                 "registered_as": row.get("registered_as")},
                window="state at observation time", source="docker-hygiene",
                observed_at=observed_at,
                why=("no owner could be confirmed for this container. On a "
                     "shared host this is the expected result and not a defect "
                     "in the classification: a container name is a string, and "
                     "a compose project label names a compose run, not an owner"),
            ))
        elif classification == "ORPHANED":
            signals.append(_signal(
                "RUNTIME_ORPHANED", f"container:{name}",
                {"classification": classification, "running": row.get("running"),
                 "reason": row.get("reason")},
                window="state at observation time", source="docker-hygiene",
                observed_at=observed_at,
                why="the owner's working directory is gone",
            ))
    return signals, {}


def _desired_signals(desired: dict, *, observed_at,
                     now=None) -> tuple[list[dict], dict, str | None]:
    """DESIRED_ACTUAL_MISMATCH, re-labelled onto the §18 vocabulary.

    `desired.py` already emits this code with its own measured block, and it
    already refuses to fire on an absent desired state (a missing declaration
    is not a mismatch). Re-labelling here rather than re-deriving keeps one
    comparison in the codebase: the rows are the ones that module produced.

    `observed_at` is the measurement's OWN time when the row carries one, and
    the detector's clock only as a fallback. This is not a detail: a daemon
    that grades its last tick's comparison — which is the normal case, and is
    the whole reason `measured` can be supplied at all — would otherwise stamp
    a four-minute-old comparison with the time it was asked for, and the two
    are the same JSON and different facts.

    Returns the signals, any gap that makes the code ungradable, and the note
    a partial comparison earns. §27, and the same defect the hygiene branch
    above was fixed for: a comparison document is TRUTHY whether or not any
    comparison in it was made, so `if desired:` graded this code on a node
    where every probe had failed and every row was UNCHECKED. `mismatches: []`
    then read as "every registration is in its desired state" — which is the
    one sentence §27 forbids, since nobody had looked.
    """
    signals: list[dict] = []
    for row in desired.get("signals") or []:
        code = row.get("code")
        if code != "DESIRED_ACTUAL_MISMATCH":
            continue
        measured_at = (row.get("measured") or {}).get("measured_at")
        at = measured_at or observed_at
        signals.append({
            **row,
            "severity": SEVERITY["DESIRED_ACTUAL_MISMATCH"],
            "source": "desired-actual",
            "observed_at": at,
            "freshness": graph.freshness_of(at, now=now or at,
                                            source="desired-actual"),
        })
    return signals, _desired_gap(desired, signals), _desired_note(desired)


def _desired_gap(desired: dict, signals: list[dict]) -> dict:
    """Why DESIRED_ACTUAL_MISMATCH could not be graded, or `{}` if it could.

    Graded means one of two things happened: the comparison reached a verdict
    on at least one registration (`in_desired_state` or `in_mismatch`), or it
    fired. Anything else is a comparison that was never made, and the document
    says so in its own coverage counters — this reads them rather than probing
    again.

    Keyed on the coverage counters and NOT on `providers.measurement.state`:
    that field reads AVAILABLE as soon as one resource has a health record,
    including the case where every axis in that record is UNAVAILABLE. Which is
    the same trap this module was written against — a key being present is not
    a measurement having succeeded.
    """
    coverage = desired.get("coverage") or {}
    compared = ((coverage.get("in_desired_state") or 0)
                + (coverage.get("in_mismatch") or 0))
    if compared or signals:
        return {}
    comparisons = coverage.get("comparisons") or 0
    providers = desired.get("providers") or {}
    measurement = providers.get("measurement") or {}
    registry = providers.get("registry") or {}
    if not comparisons:
        if registry.get("state") == "UNAVAILABLE":
            why = (registry.get("why")
                   or "no managed-runtime registry was resolved")
        else:
            why = (f"the comparison carried "
                   f"{registry.get('registrations') or 0} registration(s) and "
                   f"compared none of them")
        return {"DESIRED_ACTUAL_MISMATCH": (
            "nothing was compared, so nothing can be reported as being in its "
            f"desired state: {why}. An empty comparison is not a node with no "
            "mismatches")}
    return {"DESIRED_ACTUAL_MISMATCH": (
        f"all {comparisons} comparison(s) came back UNCHECKED, so no "
        f"registration reached a verdict and none of them was found to be in "
        f"its desired state. The comparison's own reason: "
        f"{measurement.get('why') or 'every measurement was unavailable'}. "
        f"Unmeasured is not in-state")}


def _desired_note(desired: dict) -> str | None:
    """The note a partially-compared document earns, or None.

    A document can reach a verdict on some rows and not others — Docker up for
    one resource's probe and not another's. The code is graded in that case,
    correctly, but a reader of the top-level count would take it for a
    complete comparison. So the shortfall is said out loud, by number.
    """
    coverage = desired.get("coverage") or {}
    unchecked = coverage.get("unchecked") or 0
    comparisons = coverage.get("comparisons") or 0
    if not unchecked or not comparisons:
        return None
    return (f"DESIRED_ACTUAL_MISMATCH is graded, and {unchecked} of "
            f"{comparisons} registration(s) could not be compared at all — "
            f"they are neither in their desired state nor out of it. The "
            f"counts above are over the {comparisons - unchecked} that were")


def _protected_refs(hygiene: dict) -> list[str]:
    docker = hygiene.get("docker") or {}
    return [f"container:{row.get('name')}"
            for row in (docker.get("containers") or [])
            if row.get("classification") == "PROTECTED"]


def _protected_at_risk(hygiene: dict, disk: dict, *, thresholds: dict,
                       observed_at) -> list[dict]:
    """PROTECTED_RESOURCE_AT_RISK.

    A protection that lapses produces no error, no anomaly and no line in any
    report — `PROTECTION_LOST_ON_IDENTITY_SWITCH` exists for the identity
    half of that sentence. This is the capacity half: the protected resource is
    still protected, the automatic paths still cannot touch it, and the disk
    it stands on is running out. Nobody was watching the thing the protection
    was FOR.

    The Docker data root is what makes this answerable rather than guessed:
    `disk_status()` reports which filesystem the DOCKER_FS role landed on, so
    "these protected containers stand on this filesystem" is a measurement
    rather than an inference from container names.
    """
    signals: list[dict] = []
    refs = _protected_refs(hygiene)
    if not refs:
        return signals
    danger = [row for row in _filesystem_rows(disk)
              if (row["used_percent"] or 0) >= thresholds["filesystem_percent_high"]
              or (row["inode_percent"] or 0) >= thresholds["inode_percent_high"]]
    docker_rows = [row for row in danger if "DOCKER_FS" in (row.get("roles") or [])]
    for row in docker_rows:
        for ref in refs:
            signals.append(_signal(
                "PROTECTED_RESOURCE_AT_RISK", ref,
                {"filesystem": row["subject"], "used_percent": row["used_percent"],
                 "inode_percent": row["inode_percent"],
                 "threshold_percent": thresholds["filesystem_percent_high"],
                 "protected": True},
                window="point in time", source="protected-cross-check",
                observed_at=observed_at,
                why=("this resource is declared protected AND stands on a "
                     "filesystem in a warning tier. Protection prevents the "
                     "automatic path from acting on it; it does not prevent "
                     "the disk from filling, which is what this row is for"),
            ))
    return signals


def _tunnel_signals(tunnel: dict, *, observed_at) -> tuple[list, dict]:
    """TUNNEL_UNHEALTHY, TUNNEL_OWNER_MISSING."""
    signals: list[dict] = []
    gaps: dict[str, str] = {}
    rows = list(tunnel.get("tunnels") or tunnel.get("declared") or [])
    if not rows:
        gaps["TUNNEL_UNHEALTHY"] = (
            "no declared tunnel was supplied. An empty declared set is not the "
            "same as a healthy fleet, and on this host the tunnel surface "
            "reports no declaration at all")
        gaps["TUNNEL_OWNER_MISSING"] = (
            "no declared tunnel was supplied")
        return signals, gaps
    for row in rows:
        ref = row.get("tunnel_ref") or row.get("id") or row.get("local_endpoint")
        health_row = row.get("health") or {}
        state = (health_row.get("state") if isinstance(health_row, dict)
                 else health_row)
        actual = row.get("actual_state")
        if state in TUNNEL_BAD_STATES or actual in TUNNEL_BAD_STATES:
            signals.append(_signal(
                "TUNNEL_UNHEALTHY", f"tunnel:{ref}",
                {"health_state": state, "actual_state": actual,
                 "actual_state_status": row.get("actual_state_status")},
                window="most recent observation", source="tunnel-observation",
                observed_at=observed_at,
                why=("the declaration says this tunnel exists and the "
                     "measurement does not agree. §13 forbids restarting it: "
                     "an observation that repaired what it observed would "
                     "destroy the evidence that it was ever broken"),
            ))
        owner = row.get("owner_ref")
        confidence = row.get("ownership_confidence")
        if not owner or confidence in (UNKNOWN, "UNKNOWN"):
            signals.append(_signal(
                "TUNNEL_OWNER_MISSING", f"tunnel:{ref}",
                {"owner_ref": owner, "ownership_confidence": confidence,
                 "scope": row.get("scope"), "lifecycle_class": row.get("lifecycle_class")},
                window="current declaration", source="tunnel-observation",
                observed_at=observed_at,
                why=("no owner of record for this tunnel. A process that is "
                     "forwarding has not declared itself anything, and a "
                     "nearby port or a matching name is not an owner"),
            ))
    return signals, gaps


# ---------------------------------------------------------------------------

def inputs_needed() -> list[dict]:
    """Every code, with the input this module would need to grade it.

    The static half of `codes_not_evaluated`: it says what every code this
    module grades REQUIRES, so a caller can tell "this host could not be
    measured" from "this code needs a fact this build never collects".
    """
    return [
        {"code": code, "needs": _INPUTS[code], "threshold": _threshold_for(code),
         "severity": SEVERITY[code]}
        for code in CODES
    ]


_INPUTS = {
    "DISK_WARNING": "disk_status() roles and others",
    "DISK_CRITICAL": "disk_status() roles and others",
    "INODE_CRITICAL": "inode usage on the same filesystems",
    "MEMORY_PRESSURE": "host_summary()['memory']",
    "SWAP_PRESSURE": ("host_summary()['memory']['swap'] and "
                      "host_summary()['pressure']['memory']"),
    "MEMORY_PRESSURE_ACTIVE": "host_summary()['pressure']['memory']",
    "DOCKER_STORAGE_HIGH": "docker_space() plus the data root's filesystem size",
    "RUNTIME_UNOWNED": "hygiene report container classifications",
    "RUNTIME_ORPHANED": "hygiene report container classifications",
    "DESIRED_ACTUAL_MISMATCH": "desired.py mismatch rows",
    "PROTECTED_RESOURCE_AT_RISK": "protected containers plus a graded filesystem",
    "CONTAINER_LOG_LARGE": "per-container log sizes",
    "CONTAINER_WRITABLE_LAYER_LARGE": "docker_sizes() per container",
    "BUILD_CACHE_HIGH": "docker_space() sections.build_cache",
    "TUNNEL_UNHEALTHY": "tunnel_observation() rows",
    "TUNNEL_OWNER_MISSING": "tunnel_observation() rows",
}

_THRESHOLDS = {
    "DISK_WARNING": "filesystem_percent_high",
    "DISK_CRITICAL": "filesystem_percent_critical",
    "INODE_CRITICAL": "inode_percent_high",
    "MEMORY_PRESSURE": "memory_percent_high",
    "SWAP_PRESSURE": "swap_percent_high",
    "MEMORY_PRESSURE_ACTIVE": "psi_memory_some_avg60_percent_high",
    "DOCKER_STORAGE_HIGH": "docker_storage_percent_high",
    "RUNTIME_UNOWNED": None,
    "RUNTIME_ORPHANED": None,
    "DESIRED_ACTUAL_MISMATCH": None,
    "PROTECTED_RESOURCE_AT_RISK": "filesystem_percent_high",
    "CONTAINER_LOG_LARGE": "container_log_bytes_high",
    "CONTAINER_WRITABLE_LAYER_LARGE": "container_writable_layer_bytes_high",
    "BUILD_CACHE_HIGH": "docker_build_cache_bytes_high",
    "TUNNEL_UNHEALTHY": None,
    "TUNNEL_OWNER_MISSING": None,
}


def _threshold_for(code: str):
    key = _THRESHOLDS.get(code)
    return {"key": key} if key else None


def detect(*, host=None, disk=None, docker=None, hygiene=None, desired=None,
           tunnel=None, thresholds: dict | None = None,
           observed_at=None, now=None) -> dict:
    """§18. Every code this module can grade, graded, plus the ones it cannot.

    `docker` is one document carrying the four Docker reads this needs, so that
    a caller who did not pay the ~22s for `docker system df` still gets the
    other three codes rather than an exception.

    `observed_at` is when the measurement was taken; `now` is the reader's
    clock, and the two are separated because freshness that cannot go STALE is
    not a freshness field. Aged against one instant they are equal by
    construction — every signal would report FRESH no matter how long it had
    been sitting in a file — so a caller that read a persisted observation
    passes its own clock here and gets the answer §26 is asking for. Omitting
    `now` means "read as of the observation", which is the only reading
    available when the two really are the same instant.
    """
    thresholds = thresholds or anomaly.load_thresholds()
    for key in THRESHOLD_KEYS:
        thresholds.setdefault(key, anomaly.DEFAULT_THRESHOLDS[key])
    docker = docker or {}
    hygiene = hygiene or {}
    desired = desired or {}
    tunnel = tunnel or {}
    # The clock every freshness below is aged against: the caller's, or — when
    # the caller is reading its own fresh measurement — the observation instant
    # itself. One binding, so that "which moment were these aged against" has
    # one answer in this function rather than one per branch.
    clock = now or observed_at

    gathered = _Gathered()
    signals: list[dict] = []
    levels: list[dict] = []
    notes: list[str] = []
    gaps: dict[str, str] = {}

    if gathered.take("disk", disk, why="the filesystem observation"):
        found, found_levels, note = _disk_signals(disk, thresholds, observed_at=observed_at)
        signals += found
        levels += found_levels
        if note:
            notes.append(note)
        if not found:
            notes.append("every graded filesystem is below the warning tier")
        # A document full of PRESENT-but-figureless mounts is a measurement
        # that did not happen, not a disk below its threshold. The distinction
        # is the whole of §27 and it is easy to lose here, because the shape
        # of the disk document is valid either way.
        if not any(row["used_percent"] is not None or row["inode_percent"] is not None
                   for row in _filesystem_rows(disk)):
            why = (f"the filesystem observation carried "
                   f"{len(_filesystem_rows(disk))} mount(s) and not one usage "
                   f"figure. Unread is not below the threshold")
            for code in ("DISK_WARNING", "DISK_CRITICAL", "INODE_CRITICAL",
                         "PROTECTED_RESOURCE_AT_RISK"):
                gaps.setdefault(code, why)
    else:
        for code in ("DISK_WARNING", "DISK_CRITICAL", "INODE_CRITICAL",
                     "PROTECTED_RESOURCE_AT_RISK"):
            gaps.setdefault(code, gathered.missing["disk"])

    if gathered.take("host", host, why="the host summary"):
        found, found_levels, memory_gaps = _memory_signals(host, thresholds,
                                                           observed_at=observed_at)
        signals += found
        levels += found_levels
        # Per code, not one string for the group: an unreadable PSI says nothing
        # about the two watermarks, and reporting them as unmeasured would be
        # §27's failure wearing a gap instead of a zero.
        gaps.update(memory_gaps)
    else:
        for code in ("MEMORY_PRESSURE", "SWAP_PRESSURE", "MEMORY_PRESSURE_ACTIVE"):
            gaps.setdefault(code, gathered.missing["host"])

    if docker:
        found, found_levels, docker_gaps = _docker_signals(docker, thresholds,
                                                           observed_at=observed_at)
        signals += found
        levels += found_levels
        gaps.update(docker_gaps)
    else:
        for code in ("DOCKER_STORAGE_HIGH", "BUILD_CACHE_HIGH",
                     "CONTAINER_WRITABLE_LAYER_LARGE", "CONTAINER_LOG_LARGE"):
            gaps.setdefault(code, "the Docker observation was not supplied")

    if gathered.take("hygiene", hygiene, why="the container hygiene report"):
        if gathered.take("disk", disk, why="the filesystem observation") or disk:
            signals += _protected_at_risk(hygiene, disk or {}, thresholds=thresholds,
                                          observed_at=observed_at)
        found, hygiene_gaps = _hygiene_signals(hygiene, observed_at=observed_at)
        signals += found
        # `setdefault`, so a reason already established by the report being
        # unreachable is not overwritten by the more generic one.
        for code, why in hygiene_gaps.items():
            gaps.setdefault(code, why)
    else:
        for code in ("RUNTIME_UNOWNED", "RUNTIME_ORPHANED"):
            gaps.setdefault(code, gathered.missing.get("hygiene", "the hygiene report"))

    if desired:
        found, desired_gaps, desired_note = _desired_signals(
            desired, observed_at=observed_at, now=clock)
        signals += found
        # `setdefault`, so a reason already established one layer out is not
        # overwritten by this layer's summary of it.
        for code, why in desired_gaps.items():
            gaps.setdefault(code, why)
        if desired_note:
            notes.append(desired_note)
    else:
        gaps.setdefault("DESIRED_ACTUAL_MISMATCH",
                        "the desired-actual comparison was not supplied")

    if tunnel:
        found, tunnel_gaps = _tunnel_signals(tunnel, observed_at=observed_at)
        signals += found
        gaps.update(tunnel_gaps)
    else:
        for code in ("TUNNEL_UNHEALTHY", "TUNNEL_OWNER_MISSING"):
            gaps.setdefault(code, "the tunnel observation was not supplied")

    # §26. The reader's clock, applied once, where it enters.
    #
    # Every `_signal` above aged its row against the observation instant, and
    # an observation aged against itself is FRESH by construction — so without
    # this, `freshness.state` could only ever be FRESH or UNKNOWN, and a
    # four-minute-old daemon tick read from disk would report as current. It
    # is applied here rather than passed through the fifteen call sites so that
    # a source added later cannot be the one that forgets.
    if clock is not None:
        for row in signals:
            row["freshness"] = graph.freshness_of(
                row.get("observed_at"), now=clock, source=row.get("source"))

    emitted = sorted({row["code"] for row in signals})
    # A code that was graded and found nothing is NOT the same as a code whose
    # input never arrived. `graded` is the first set; `gaps` is the second.
    graded = [code for code in CODES if code not in gaps]
    ungraded = [{"code": code, "why": gaps[code],
                 "needs": _INPUTS[code], "threshold": _threshold_for(code)}
                for code in CODES if code in gaps]

    signals.sort(key=lambda row: (row["severity"] != "high",
                                  row["severity"] != "medium",
                                  row["code"], row["subject"]))
    # §21. What every GRADED measurement is, whether or not it crossed a tier.
    # Keyed by `code|subject` because that pair is the alert identity and the
    # two tables are read together: `signals` says what fired, `levels` says
    # what everything is, and an alert closes on a `levels` row rather than on
    # a signal's absence — see `runtime/alerting.py`.
    by_level = {f"{row['code']}|{row['subject']}": row for row in levels}
    return {
        "observed_at": observed_at,
        "signals": signals,
        "levels": by_level,
        "by_code": {code: sum(1 for r in signals if r["code"] == code)
                    for code in emitted},
        "codes_emitted": emitted,
        "codes_graded": graded,
        "codes_not_evaluated": ungraded,
        "coverage": {
            "graded": len(graded),
            "of": len(CODES),
            "note": ("a code in `codes_graded` was looked for and not found; a "
                     "code in `codes_not_evaluated` was not looked for at all. "
                     "Reading the second as the first is how an unmeasured "
                     "host reports as a clean one"),
        },
        "notes": notes,
        "advisory": (
            "Every row here is a measurement with its threshold beside it. "
            "Nothing in this module acts on any of them: there is no function "
            "with a verb in it, and §25 forbids the two the brief names by "
            "name (`high CPU => kill`, `high memory => kill`). The strongest "
            "permitted consequence of DISK_CRITICAL is refusing the creation "
            "of a NEW disposable resource."
        ),
    }


# ---------------------------------------------------------------------------
# §25 · the one permitted consequence, and the two forbidden ones
# ---------------------------------------------------------------------------
#
# §25 draws a line that is easy to write and easy to cross by accident: a
# pressure measurement may refuse the creation of something NEW, and may never
# act on something that already exists. The line is drawn here as a shape
# rather than a rule — `admission()` has no argument that names an existing
# resource, so "kill the container that is using the memory" is not a thing
# this module can express, not a thing it declines to do.

FORBIDDEN_RESPONSES = ("high CPU => kill", "high memory => kill")

# The contract's `resource_admission.gates`, transcribed for the same reason
# every other table here is: so the contract can be read without opening the
# module, and so the two can be compared by a test instead of by a reviewer.
# The levels are exactly two, and `refuse` is the only one that changes an
# answer; everything not in this table is not an admission question at all.
#
# Graded on WHAT THE CODE MEASURES, not inherited from what its predecessor
# measured. A full machine is a reason to refuse the next persistent process;
# a full SWAP FILE is not, which is why `SWAP_PRESSURE` moved to `advisory`
# when its name was narrowed to what it had always graded. What the codes mean
# respectively, and why the two watermarks do not get the same answer:
#
#   MEMORY_PRESSURE         `used_bytes` is total minus MemAvailable, and
#                           MemAvailable is the kernel's own estimate with
#                           reclaimable cache already subtracted — a low figure
#                           is memory the machine genuinely cannot get back.
#   SWAP_PRESSURE           a high-water mark that only rises. Pages are
#                           written to swap when memory was short and read back
#                           only when touched again, so a swap file that filled
#                           once stays full while nothing is paging. Measured
#                           on this host 2026-09-15: 99.2% of swap in use,
#                           memory PSI 0.00 across every window, `pswpout`
#                           unmoved over forty minutes.
#   MEMORY_PRESSURE_ACTIVE  work is stalling NOW. A machine in that state
#                           should not be handed another persistent process,
#                           and refusing one is the only consequence §25
#                           permits.
ADMISSION_LEVELS = {
    "DISK_CRITICAL": "refuse",
    "INODE_CRITICAL": "refuse",
    "MEMORY_PRESSURE": "refuse",
    "MEMORY_PRESSURE_ACTIVE": "refuse",
    "SWAP_PRESSURE": "advisory",
    "DISK_WARNING": "advisory",
    "DOCKER_STORAGE_HIGH": "advisory",
    "BUILD_CACHE_HIGH": "advisory",
}

REFUSING_CODES = tuple(sorted(c for c, level in ADMISSION_LEVELS.items()
                              if level == "refuse"))
ADVISORY_CODES = tuple(sorted(c for c, level in ADMISSION_LEVELS.items()
                              if level == "advisory"))

# The three answers. There is no fourth: "yes, and do something about it" is
# the shape §25 removes.
ADMIT = "ALLOW"
REFUSE = "REFUSE_NEW_CREATION"
NOT_ASSESSED = "NOT_ASSESSED"


def load_admission(path=None) -> dict:
    """§25's gate levels from the contract, over the transcription.

    Same contract-over-defaults shape as `anomaly.load_thresholds`: an
    unreadable contract keeps the built-in table, and a test asserts the two
    still agree so the duplication fails loudly rather than drifting.
    """
    levels = dict(ADMISSION_LEVELS)
    try:
        import yaml

        document = yaml.safe_load(Path(path or CONTRACT).read_text()) or {}
    except (OSError, Exception):  # noqa: BLE001 - unreadable contract keeps defaults
        return levels
    gates = ((document.get("resource_admission") or {}).get("gates") or {})
    for code, entry in gates.items():
        level = (entry or {}).get("level")
        if level:
            levels[code] = level
    return levels


def admission(detection: dict, *, request: str | None = None,
              levels: dict | None = None) -> dict:
    """§25 — whether something NEW may be created here, given a detection.

    Pure over `detect`'s output. It reads no host, takes no action, and has no
    argument for a resource that exists: the only decision available is about a
    creation that has not happened yet.

    The three answers, and why the middle one is not `ALLOW`:

    `REFUSE_NEW_CREATION` — a gate at the `refuse` level fired. A caller that
    honours it does nothing to what is running; it declines to add.

    `ALLOW` — every refusing gate was graded and none fired.

    `NOT_ASSESSED` — no refusing gate fired, and at least one was never graded
    because its input did not arrive. This is `codes_not_evaluated` read as an
    admission answer, and it exists because the alternative is the defect §27
    names one level down: a gate whose input is missing reports exactly like a
    gate that passed. `ALLOW` here would mean "this host will take another one",
    said by a process that never looked at the disk.

    NOT WIRED, and the contract says so in `wired_into_a_creation_path: false`.
    Nothing in this tree calls this function; a test asserts that, which is what
    makes the contract's claim a checked one rather than a hopeful one. §25
    permits the contract and the tests when wiring would expand the round, and
    it would: the build runner, the test-environment provisioner and the session
    launcher each owe their callers a story about what a refusal means
    mid-flight, and that story is Resource Governance V1's (§38).
    """
    levels = levels or load_admission()
    signals = (detection or {}).get("signals") or []
    covered = (detection or {}).get("codes_not_evaluated")
    if covered is None:
        # A detection that carries no coverage report is not a detection that
        # covered everything. Absence of the key is treated the same as absence
        # of an input, because the alternative — reading a missing report as a
        # clean one — is the defect §27 names, one level down.
        ungraded = {code: "the detection carried no coverage report"
                    for code in ADMISSION_LEVELS}
    else:
        ungraded = {row.get("code"): row.get("why") for row in covered}

    refusing = {code for code, level in levels.items() if level == "refuse"}
    advisory = {code for code, level in levels.items() if level == "advisory"}

    blocking = [row for row in signals if row.get("code") in refusing]
    advising = [row for row in signals if row.get("code") in advisory]
    # A refusing gate whose input never arrived. Sorted so two calls with the
    # same missing inputs answer the same way.
    unanswered = [{"code": code, "why": ungraded.get(code)}
                  for code in sorted(refusing) if code in ungraded]

    if blocking:
        decision = REFUSE
    elif unanswered:
        decision = NOT_ASSESSED
    else:
        decision = ADMIT

    return {
        "decision": decision,
        # A label for what is about to be created, echoed back so a caller's log
        # says which kind it asked about. It does not select a gate: the
        # contract's `request_kinds` refuses every kind at the same tier, so a
        # caller cannot buy a cheaper answer by describing the thing as small.
        "request": request,
        "blocking": blocking,
        "advisory": advising,
        "not_assessed": unanswered,
        "clear": sorted(refusing - {row.get("code") for row in blocking}
                        - {row["code"] for row in unanswered}),
        "acts_on_existing_resources": False,
        "forbidden": list(FORBIDDEN_RESPONSES),
        "wired_into_a_creation_path": False,
        "next_round": "Resource Governance V1 (§38)",
        "observed_at": (detection or {}).get("observed_at"),
        "note": ("This answer is about adding something new. It is not a "
                 "recommendation about anything that already exists, and the "
                 "two forbidden responses are inexpressible here rather than "
                 "merely declined."),
    }


if __name__ == "__main__":  # pragma: no cover - manual inspection only
    import json

    print(json.dumps({"codes": list(CODES), "severity": SEVERITY,
                      "inputs": _INPUTS}, indent=2, ensure_ascii=False))
