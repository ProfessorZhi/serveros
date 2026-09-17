#!/usr/bin/env python3
"""Host resource facts — measured, never estimated, and never auto-remediated.

Three rules, each of which is a thing that has gone wrong on this machine.

**Unobservable is a result.** There is no GPU here (`/dev/dri/card0` is a
Cirrus Logic virtual framebuffer and `nvidia-smi` does not exist). That is
reported as `UNAVAILABLE` with the reason, not as zero. A `0` in a GPU field
reads as "no load"; it is a claim this host cannot make.

**Every command has a budget.** `docker system df` was measured on this host
at over 120 seconds — it did not finish inside the shell budget while this
module was being written. A resource inventory that can hang is worse than one
that reports `UNAVAILABLE`, because the thing it is inventorying is usually
being asked a question during an incident.

**Root pressure is reported, never acted on.** Nothing in this module removes
anything, and nothing downstream of it may either: the automatic path is
bounded to resources some owner registered as disposable, and unknown files
are not a category it can reach. `pressure()` produces text for a person.

This docstring used to state the two filesystems' percentages — "47% used" and
"61%" — and by the time it was next read they were 53.6% and 64.3%. A number
transcribed into prose is a number nobody measures again, so the module no
longer carries any. Ask `disk_status()`; it looks every time.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
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


# The root contract. `HOME` is a root the contract names, not a string this
# module gets to spell: the question below is "does HOME share the root
# filesystem", and answering it with a hardcoded path answered it for one
# machine and silently answered NO on every other.
_paths = _load("serveros_runtime_paths", "paths.py")


registry = _load("serveros_runtime_registry", "registry.py")

RegistryError = registry.RegistryError

# Budgets. Chosen so that a full inventory finishes well inside a person's
# patience during an incident, and so that one slow subsystem degrades one
# field instead of the whole report.
BUDGET = {
    "fast": 8.0,      # /proc reads via small tools
    "docker": 25.0,   # docker ps / inspect / stats
    "df": 10.0,
    # `docker system df` measured at 19.3-22.2s on this host (2026-09-14)
    # against 0.09s for `docker info`. It gets its own budget so that its cost
    # cannot be paid by — or charged to — the routine reads above.
    "docker_space": 45.0,
}

UNAVAILABLE = "UNAVAILABLE"


class InventoryError(RegistryError):
    """A measurement could not be taken."""


def _run(args: list[str], timeout: float) -> tuple[int, str, str]:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {timeout}s"
    except (OSError, subprocess.SubprocessError) as exc:
        return -2, "", f"{type(exc).__name__}: {exc}"
    return out.returncode, out.stdout, out.stderr


def _unavailable(why: str, **extra) -> dict:
    return {"state": UNAVAILABLE, "reason": why, **extra}


# Docker prints sizes two ways and they are NOT the same scale. `docker images`
# and `docker system df` use SI suffixes ("1.62GB" = 1.62e9), while `docker
# stats` uses IEC ones ("1.62GiB" = 1.62 * 2^30). A parser that treats the
# suffix as decoration is wrong by 7% on every number, consistently, in the
# direction of understating how much disk is in use.
_SIZE_UNITS = {
    "B": 1,
    "kB": 1000, "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4,
    "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4,
}


def _parse_size(text) -> int | None:
    """Docker's human size to bytes, or None when there is no number.

    `N/A` and empty strings are returned as None rather than 0. `SharedSize`
    and `UniqueSize` are `N/A` on this host for every image; reporting those as
    0 bytes would say "this image shares nothing", which is a different and
    false claim.
    """
    if text is None:
        return None
    token = str(text).strip()
    if not token or token in ("N/A", "<none>", "-"):
        return None
    for unit in sorted(_SIZE_UNITS, key=len, reverse=True):
        if token.endswith(unit):
            number = token[:-len(unit)].strip()
            try:
                return int(float(number) * _SIZE_UNITS[unit])
            except ValueError:
                return None
    try:
        return int(float(token))
    except ValueError:
        return None


def _parse_percent(text) -> float | None:
    """`"12.34%"` to `12.34`. Anything else is None, never 0.0.

    A container whose CPU reading is missing must not be reported as idle: one
    of those is a resource that needs looking at, and the other is a resource
    that is fine.
    """
    if text is None:
        return None
    token = str(text).strip().rstrip("%")
    try:
        return float(token)
    except ValueError:
        return None


def _parse_ps_size(text) -> tuple[int | None, int | None]:
    """`"77.7MB (virtual 1.28GB)"` to `(writable_bytes, root_fs_bytes)`.

    Returns `(None, None)` for an absent field. Note the container with nothing
    written to it prints `"0B (virtual 1.22GB)"` — a real zero, which is
    different from a missing size, and both are representable in the result.
    """
    if text is None:
        return None, None
    token = str(text).strip()
    if not token:
        return None, None
    writable, _, rest = token.partition("(virtual")
    root = rest.rstrip(") ") if rest else None
    return _parse_size(writable), _parse_size(root)


def _int_or_none(text) -> int | None:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def _slug(text) -> str | None:
    """`"Local Volumes"` to `"local_volumes"`. None when there is no name."""
    if text is None:
        return None
    slug = "_".join(str(text).strip().lower().split())
    return slug or None


def _parse_df_table(text: str) -> list[dict]:
    """The plain `docker system df` table, as the rows the JSON form would give.

    Columns are separated by RUNS of two or more spaces, not by single spaces,
    because the table has multi-word cells on both ends: `TYPE` is
    `"Local Volumes"` and `RECLAIMABLE` is `"177.7GB (92%)"`. Splitting on runs
    is what makes that unambiguous — a positional split from either end assigns
    `83.57GB` as the SIZE of `"Local"` and everything else to the wrong column.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    columns = [c.strip() for c in re.split(r"\s{2,}", lines[0].strip())]
    if "TYPE" not in columns:
        return []
    # The table's headers are not the JSON form's keys, and the caller reads one
    # set of names. Translating here is what makes this a fallback rather than
    # a second parser with its own vocabulary.
    names = {"TYPE": "Type", "TOTAL": "TotalCount", "ACTIVE": "Active",
             "SIZE": "Size", "RECLAIMABLE": "Reclaimable"}
    rows = []
    for line in lines[1:]:
        cells = [c.strip() for c in re.split(r"\s{2,}", line.strip())]
        if len(cells) != len(columns):
            continue
        rows.append({names.get(c, c): v for c, v in zip(columns, cells)})
    return rows


def _at_least(measured, threshold) -> bool:
    """Is `measured` at or above a declared threshold?

    A missing threshold is **not** zero. A contract that failed to load, or a
    key that was renamed, must produce no note rather than a note about every
    reading — "we have no rule" and "you have broken the rule" are different
    statements, and only one of them is a reason to act.
    """
    if measured is None or threshold is None:
        return False
    try:
        return float(measured) >= float(threshold)
    except (TypeError, ValueError):
        return False


def _psi_avg60(kind) -> float | None:
    """One PSI window, or None when the kernel did not give one.

    None is not zero and the caller must not treat it as zero: a `some` line
    with no `avg60`, or a value the parser could not turn into a number, is a
    measurement that did not happen.
    """
    if not isinstance(kind, dict):
        return None
    value = kind.get("avg60")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _gib(value) -> str:
    if not isinstance(value, (int, float)):
        return "an unreadable amount"
    return f"{value / (1024 ** 3):.1f} GiB"


def _swap_note(swap, swap_high, psi, limits, mem) -> str:
    """What a full swap file means — which is not always the same thing.

    **A watermark and a pressure are two different facts, and a note that
    reports the first as the second is a note nobody can act on correctly.**
    `used_percent` is a high-water mark. Pages are written to swap when memory
    was short and read back only when they are touched again, so a swap file
    that filled once stays full for as long as the pages sitting in it are
    never needed. Read alone it says what happened to this machine, not what
    is happening to it — and this host is the worked example, measured
    2026-09-15: 99.2% of an 8.0 GiB swap file in use, memory PSI at
    some/full avg10/avg60/avg300 all 0.00, `pswpout` at 3208181 across two
    readings forty minutes apart, and 15.7 GiB of 31.0 GiB still available.
    Nothing was paging. The note said "Real memory pressure".

    So the watermark is a reason to ASK, and memory PSI is the answer. The
    three outcomes are stated separately rather than folded into one sentence,
    because they call for different responses: real pressure needs the process
    to justify itself, a stale watermark needs nothing, and an unreadable PSI
    needs the reader to know that nobody established it.

    Deliberately NOT saying "no pressure" when PSI is absent. `/proc/pressure`
    is missing on a pre-4.20 kernel and under `psi=0`, and "cannot tell" is
    not "fine" — that conflation is the same one `pressure_stall` refuses to
    make one method above, and it would not become true by being made in a
    sentence instead of a field.
    """
    watermark = (
        f"swap is {swap['used_percent']}% used "
        f"({swap['used_bytes']} of {swap['total_bytes']} bytes, "
        f"threshold {swap_high}%)."
    )
    some_high = limits.get("psi_memory_some_avg60_percent_high")
    full_high = limits.get("psi_memory_full_avg60_percent_high")
    some, full = _psi_avg60((psi or {}).get("some")), _psi_avg60((psi or {}).get("full"))

    if some is None or full is None:
        why = (psi or {}).get("reason") if isinstance(psi, dict) else None
        return (
            f"{watermark} Whether that is pressure NOW was not established: "
            f"{why or 'memory pressure-stall information was not readable'}. "
            f"A watermark is not a measurement of pressure, and this note will "
            f"not guess which one it is looking at."
        )
    stall = (
        f"memory stall is some avg60={some}% (threshold {some_high}%), "
        f"full avg60={full}% (threshold {full_high}%)"
    )
    if some >= some_high or full >= full_high:
        return (
            f"{watermark} Real memory pressure, and measured as such: {stall}, "
            f"with {_gib(mem.get('available_bytes'))} available of "
            f"{_gib(mem.get('total_bytes'))}. A new persistent process has to "
            f"justify itself."
        )
    return (
        f"{watermark} That is a full swap file, not current memory pressure: "
        f"{stall}, {_gib(mem.get('available_bytes'))} available of "
        f"{_gib(mem.get('total_bytes'))}. Pages are read back from swap only "
        f"when they are touched again, so a swap file that filled once stays "
        f"full while nothing is paging. This number says what happened to this "
        f"machine, not what is happening to it."
    )


class LiveHost:
    """Shell access with budgets. Read-only by construction: no method here
    has any means of writing, and this module imports no mutating verb."""

    name = "live-host"

    def read(self, args: list[str], timeout: float) -> tuple[int, str, str]:
        return _run(args, timeout)

    def read_text(self, path: str) -> str | None:
        try:
            return Path(path).read_text()
        except OSError:
            return None

    def statvfs(self, path: str):
        try:
            return shutil.disk_usage(path)
        except OSError:
            return None

    def inode_usage(self, path: str):
        """Nodes, which are a separate exhaustion axis from bytes.

        A filesystem with free space and no free inodes cannot create a file,
        and `shutil.disk_usage` cannot see that. `os.statvfs` is the stdlib
        call that reports it, and it is read here rather than shelled out to
        `df` so that the number is the same one `df` would print — not a second
        parsing of the same kernel data.

        `f_files` is 0 on filesystems that do not report inodes (some network
        and pseudo filesystems). That is reported as nulls, not as 0% used:
        "no inode budget" and "an empty inode budget" are different facts.
        """
        try:
            st = os.statvfs(path)
        except OSError:
            return None
        if not st.f_files:
            return {"total": None, "free": None, "used": None, "used_percent": None}
        used = st.f_files - st.f_ffree
        return {
            "total": st.f_files,
            "free": st.f_ffree,
            "used": used,
            "used_percent": round(100.0 * used / st.f_files, 1),
        }

    def path_stat(self, path: str) -> dict:
        """Size and mtime of one file, or a named reason there is neither.

        Never raises and never returns None, because "we could not look" is the
        ordinary case for Docker's own data root here — it is `0710 root:root`
        and this process is not root. A caller that had to catch an exception
        would eventually catch it somewhere that turns it into a zero.

        `PermissionError` is separated from every other `OSError` on purpose:
        "this file is in a directory we may not enter" and "this file does not
        exist" have different responses, and `errno` is the only place the
        difference survives.
        """
        try:
            st = os.stat(path)
        except PermissionError:
            return {"state": "PERMISSION_DENIED",
                    "reason": ("the file exists in the daemon's own tree but a "
                               "directory on the way to it is not traversable "
                               "by this process")}
        except OSError as exc:
            return {"state": UNAVAILABLE, "reason": f"{type(exc).__name__}: {exc}"}
        return {"state": "PRESENT", "size_bytes": st.st_size,
                "modified_at": st.st_mtime}

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def sleep(self, seconds: float) -> None:
        """The gap between two counter reads.

        A method rather than a bare `time.sleep` so that a test can measure a
        counter delta without waiting for wall-clock time to pass — and so the
        interval a caller asked for and the interval that elapsed cannot drift
        apart without the seam being visible.
        """
        time.sleep(seconds)


class Inventory:
    """Machine primitives. Every method returns structured data or a named
    UNAVAILABLE — never a zero standing in for "we did not measure"."""

    # Filesystems are labelled by ROLE, not by device name, because the role
    # is what policy depends on. `/dev/vda2` is not meaningful to a policy
    # that says "protect the system disk"; `ROOT_FS` is.
    #
    # These two are declared because they are the same path on every host this
    # product runs on. The other three are LOOKED UP, because they are not: see
    # `_resolved_roles`.
    FILESYSTEM_ROLES = {
        "/": "ROOT_FS",
        "/opt/applications": "DATA_FS",
    }

    # Roots that come from `paths.Paths()` rather than from a constant. Keyed
    # by the role a policy would name, valued by the root name that `paths.py`
    # declares — so a root that moves is a change in one contract file and not
    # a search for a second copy of the path.
    SEMANTIC_FILESYSTEM_ROLES = {
        "STATE_FS": "state",
        "ARTIFACTS_FS": "artifacts",
    }

    # The Docker data root, which is emphatically NOT a constant. `/var/lib/docker`
    # is the default on a distribution that did not move it, and the whole point
    # of asking `docker info` is that this host may not be that distribution.
    DOCKER_ROLE = "DOCKER_FS"

    def __init__(self, host=None) -> None:
        self.host = host if host is not None else LiveHost()
        # Resolved once per instance: every caller in one inventory run must see
        # the same answer, and `docker info` is not free.
        self._roles_cache: list[dict] | None = None

    # -- host --------------------------------------------------------------

    def host_summary(self) -> dict:
        return {
            "cpu": self._cpu(),
            "memory": self._memory(),
            "load": self._load(),
            "uptime_seconds": self._uptime(),
            "gpu": self._gpu(),
        }

    def _cpu(self) -> dict:
        text = self.host.read_text("/proc/cpuinfo")
        if text is None:
            return _unavailable("/proc/cpuinfo is not readable")
        model = None
        logical = 0
        cores_per_socket = None
        siblings = None
        for line in text.splitlines():
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if key == "processor":
                logical += 1
            elif key == "model name" and model is None:
                model = value
            elif key == "cpu cores":
                cores_per_socket = int(value)
            elif key == "siblings":
                siblings = int(value)
        return {
            "model": model,
            "logical_cpus": logical,
            "cores_per_socket": cores_per_socket,
            "siblings_per_socket": siblings,
            # Only computable when both were readable; otherwise named absent
            # rather than guessed at.
            "threads_per_core": (
                siblings // cores_per_socket
                if cores_per_socket and siblings
                else UNAVAILABLE
            ),
        }

    def _memory(self) -> dict:
        text = self.host.read_text("/proc/meminfo")
        if text is None:
            return _unavailable("/proc/meminfo is not readable")
        values: dict[str, int] = {}
        for line in text.splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                # /proc/meminfo is in kB.
                values[key.strip()] = int(parts[0]) * 1024
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        swap_total = values.get("SwapTotal")
        swap_free = values.get("SwapFree")
        out = {
            "total_bytes": total,
            "used_bytes": (total - available) if (total and available is not None) else None,
            "available_bytes": available,
            "free_bytes": values.get("MemFree"),
            "cached_bytes": values.get("Cached"),
            "swap": {
                "total_bytes": swap_total,
                "free_bytes": swap_free,
                "used_bytes": (
                    (swap_total - swap_free)
                    if (swap_total is not None and swap_free is not None)
                    else None
                ),
            },
        }
        # Swap exhaustion is reported as its own fact rather than folded into
        # "memory". This host runs at ~99.6% swap with a load average above
        # its core count, and a memory report that only shows RAM would call
        # that healthy.
        if swap_total:
            out["swap"]["used_percent"] = round(
                100.0 * (swap_total - swap_free) / swap_total, 1
            )
        return out

    def _load(self) -> dict:
        text = self.host.read_text("/proc/loadavg")
        if text is None:
            return _unavailable("/proc/loadavg is not readable")
        parts = text.split()
        return {
            "load1": float(parts[0]),
            "load5": float(parts[1]),
            "load15": float(parts[2]),
            "runnable": parts[3] if len(parts) > 3 else None,
        }

    def _uptime(self) -> float | None:
        text = self.host.read_text("/proc/uptime")
        if text is None:
            return None
        try:
            return float(text.split()[0])
        except (ValueError, IndexError):
            return None

    # -- cpu usage ---------------------------------------------------------
    #
    # Not a profiler and not a sampler loop: `/proc/stat` is a set of counters,
    # so a usage figure needs two reads and the interval between them. The
    # interval is returned alongside the number, because "37% over 150ms" and
    # "37% over a minute" are different measurements and a reader who is not
    # told which one they have cannot argue with either.

    CPU_SAMPLE_SECONDS = 0.15

    def cpu_usage(self, interval: float | None = None) -> dict:
        window = self.CPU_SAMPLE_SECONDS if interval is None else interval
        first = self._cpu_counters()
        if first is None:
            return _unavailable("/proc/stat is not readable")
        self.host.sleep(window)
        second = self._cpu_counters()
        if second is None:
            return _unavailable("/proc/stat stopped being readable mid-sample")
        return self._cpu_delta(first, second, window)

    def _cpu_counters(self) -> dict | None:
        text = self.host.read_text("/proc/stat")
        if text is None:
            return None
        for line in text.splitlines():
            if not line.startswith("cpu "):
                continue
            fields = line.split()[1:]
            if len(fields) < 4:
                return None
            try:
                values = [int(f) for f in fields]
            except ValueError:
                return None
            names = ("user", "nice", "system", "idle", "iowait", "irq",
                     "softirq", "steal", "guest", "guest_nice")
            return dict(zip(names, values))
        return None

    @staticmethod
    def _cpu_delta(first: dict, second: dict, window: float) -> dict:
        keys = sorted(set(first) | set(second))
        total = sum(second.get(k, 0) - first.get(k, 0) for k in keys)
        # `iowait` is idle time. Counting it as busy is how a machine waiting on
        # a disk gets reported as a machine doing work.
        idle = second.get("idle", 0) - first.get("idle", 0)
        iowait = second.get("iowait", 0) - first.get("iowait", 0)
        if total <= 0:
            # A zero delta is not 0% usage — it is a sample that caught no
            # ticks, and the two must not be confused.
            return _unavailable(
                "no jiffies elapsed between the two /proc/stat reads",
                sample_seconds=window)
        busy = total - idle - iowait
        return {
            "state": "PRESENT",
            "used_percent": round(100.0 * busy / total, 1),
            "busy_jiffies": busy,
            "total_jiffies": total,
            "sample_seconds": window,
            "counters": {k: second.get(k, 0) - first.get(k, 0) for k in keys},
        }

    # -- pressure stall information ----------------------------------------

    PSI_RESOURCES = ("cpu", "memory", "io")

    def pressure_stall(self) -> dict:
        """PSI, where the kernel provides it.

        This is the kernel's own answer to "how much did work stall waiting",
        which is a different question from "how full is it". A host at 40% disk
        with a stalled scheduler and a host at 40% disk doing nothing are the
        same number and are not the same machine.

        Absent `/proc/pressure` (pre-4.20, or `psi=0` on the command line or in
        the container's cgroup) is reported as UNAVAILABLE per resource. It is
        never reported as zero: "no stall" and "cannot tell" are different.
        """
        out: dict = {}
        seen = 0
        for resource in self.PSI_RESOURCES:
            text = self.host.read_text(f"/proc/pressure/{resource}")
            if text is None:
                out[resource] = _unavailable(
                    f"/proc/pressure/{resource} is not readable; the kernel "
                    f"may predate PSI or it may be disabled")
                continue
            seen += 1
            parsed: dict = {"state": "PRESENT"}
            for line in text.splitlines():
                kind, _, rest = line.partition(" ")
                if kind not in ("some", "full"):
                    continue
                values = {}
                for token in rest.split():
                    key, _, value = token.partition("=")
                    try:
                        values[key] = float(value)
                    except ValueError:
                        values[key] = None
                parsed[kind] = values
            out[resource] = parsed
        out["provider"] = "PRESENT" if seen else UNAVAILABLE
        return out

    # -- oom ---------------------------------------------------------------

    def oom_evidence(self) -> dict:
        """Whether the kernel has had to kill anything, and whether swap is
        the reason it might.

        `oom_kill` in `/proc/vmstat` is cumulative since boot, so it is a fact
        about the machine's history and not about now. Presented as what it is,
        with uptime alongside so a reader can normalise it.
        """
        text = self.host.read_text("/proc/vmstat")
        if text is None:
            return _unavailable("/proc/vmstat is not readable")
        count = None
        for line in text.splitlines():
            key, _, value = line.partition(" ")
            if key == "oom_kill":
                try:
                    count = int(value)
                except ValueError:
                    count = None
                break
        mem = self._memory()
        swap = (mem.get("swap") or {}) if isinstance(mem, dict) else {}
        return {
            "state": "PRESENT" if count is not None else UNAVAILABLE,
            "oom_kills_since_boot": count,
            "uptime_seconds": self._uptime(),
            "swap_used_percent": swap.get("used_percent"),
            "swap_used_bytes": swap.get("used_bytes"),
            "note": ("cumulative since boot, not a rate. A non-zero count on a "
                     "long-lived host is history, not current pressure."),
        }

    # -- disk io -----------------------------------------------------------

    DISKSTATS_KEYS = ("read_bytes", "write_bytes", "read_ios", "write_ios")

    def disk_io(self, interval: float | None = None) -> dict:
        """Read/write activity for the devices that back the role mounts.

        Only the tracked devices, because "the whole machine did 4 GB of IO" is
        not actionable and "DATA_FS is doing 90 MB/s while ROOT_FS is idle" is.
        `/proc/diskstats` counts 512-byte sectors regardless of the device's
        own sector size; the multiplication here is the kernel's convention,
        not an assumption about the hardware.
        """
        window = self.CPU_SAMPLE_SECONDS if interval is None else interval
        devices = self._role_devices()
        first = self._diskstats()
        # The gap is REAL. Two reads taken back to back produce a delta over
        # some microseconds, and a rate computed from it is a number about
        # nothing — the counters would usually not have moved at all, and
        # "0 B/s" would be reported on a disk that is busy.
        self.host.sleep(window)
        second = self._diskstats()
        if first is None or second is None:
            return _unavailable("/proc/diskstats is not readable")
        elapsed = self._elapsed(first, second, window)
        if elapsed is None:
            return _unavailable("the two /proc/diskstats reads share no clock")
        rows: dict = {}
        for role, dev in sorted(devices.items()):
            before, after = first["devices"].get(dev), second["devices"].get(dev)
            if before is None or after is None:
                rows[role] = _unavailable(f"no diskstats row for {dev}")
                continue
            delta = {k: after.get(k, 0) - before.get(k, 0)
                     for k in self.DISKSTATS_KEYS}
            rows[role] = {
                "state": "PRESENT",
                "device": dev,
                "read_bytes_per_second": round(delta["read_bytes"] / elapsed, 1),
                "write_bytes_per_second": round(delta["write_bytes"] / elapsed, 1),
                "read_iops": round(delta["read_ios"] / elapsed, 1),
                "write_iops": round(delta["write_ios"] / elapsed, 1),
            }
        return {
            "roles": rows,
            "pressure": self.pressure_stall().get("io"),
            "sample_seconds": round(elapsed, 4),
            "note": ("only the devices behind the role mounts are reported; "
                     "a machine-wide total answers no question an operator has"),
        }

    @staticmethod
    def _elapsed(first: dict, second: dict, requested: float) -> float | None:
        """How long the sample actually took, from the clock in the samples.

        Preferred over the interval that was asked for: `sleep(0.15)` returns
        after *at least* 0.15s, and on a loaded host the difference is exactly
        the load. Returning the requested interval would hide the machine's
        own slowness inside a rate.
        """
        if first.get("at") is not None and second.get("at") is not None:
            measured = second["at"] - first["at"]
            if measured > 0:
                return measured
        return requested if requested > 0 else None

    def _diskstats(self) -> dict | None:
        """Counters for every device, with the clock they were read at.

        The clock is carried at the top level rather than per row: every row in
        one read was read at the same instant, and a per-row copy invites the
        reader to subtract two rows that came from different reads.
        """
        text = self.host.read_text("/proc/diskstats")
        if text is None:
            return None
        out: dict = {}
        for line in text.splitlines():
            fields = line.split()
            if len(fields) < 14:
                continue
            name = fields[2]
            # Whole-disk rows and partition rows are both kept, and neither is
            # summed here: a caller looks up the exact device `findmnt`
            # reported, so `vda2` and `vda` are two answers to two questions
            # rather than one answer counted twice.
            try:
                out[name] = {
                    "read_ios": int(fields[3]),
                    "read_bytes": int(fields[5]) * 512,
                    "write_ios": int(fields[7]),
                    "write_bytes": int(fields[9]) * 512,
                }
            except (ValueError, IndexError):
                continue
        return {"at": self._monotonic(), "devices": out}

    def _monotonic(self) -> float | None:
        """Seconds since boot, read through `self.host`.

        Through the host rather than straight off the filesystem so that a test
        can hand this module a clock. A sample whose interval is read from the
        real machine while its counters come from a fixture measures nothing.
        """
        text = self.host.read_text("/proc/uptime")
        if text is None:
            return None
        try:
            return float(text.split()[0])
        except (ValueError, IndexError):
            return None

    def _role_devices(self) -> dict:
        """Which block device backs each role mount, by name.

        Resolved, not assumed: Docker's data root is wherever `docker info`
        says it is, and a policy that hardcodes `/var/lib/docker` is a policy
        about a machine that may not be this one.
        """
        out = {}
        for mount in self._resolved_roles():
            path = mount["path"]
            if path is None:
                # An unresolved role is an absence of question, not an absence
                # of device, and `findmnt --target None` would answer about a
                # directory named `None` if one happened to exist.
                continue
            code, stdout, _ = self.host.read(
                ["findmnt", "-no", "SOURCE", "--target", path], BUDGET["fast"])
            if code != 0 or not stdout.strip():
                continue
            source = stdout.split()[0]
            if source.startswith("/dev/"):
                out[mount["role"]] = source.rsplit("/", 1)[-1]
        return out

    def _gpu(self) -> dict:
        code, out, err = self.host.read(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                                        "--format=csv,noheader"], BUDGET["fast"])
        if code == 0 and out.strip():
            cards = []
            for line in out.strip().splitlines():
                fields = [f.strip() for f in line.split(",")]
                cards.append(dict(zip(
                    ("model", "vram_total_mib", "vram_used_mib", "utilization_percent"),
                    fields,
                )))
            return {"state": "PRESENT", "compute_capable": True, "cards": cards}
        # No driver. Say what IS there, because the two failure modes are
        # different: a machine with no GPU at all, and a machine with a GPU
        # and no compute driver.
        display = []
        lspci_code, lspci_out, _ = self.host.read(["lspci"], BUDGET["fast"])
        if lspci_code == 0:
            display = [
                ln.split(":", 2)[-1].strip()
                for ln in lspci_out.splitlines()
                if any(k in ln.lower() for k in ("vga", "3d controller", "display"))
            ]
        return {
            "state": UNAVAILABLE,
            "compute_capable": False,
            "reason": (
                "no compute driver reachable (nvidia-smi did not run). This "
                "host may still have a virtual display adapter, which is not "
                "a GPU in the resource sense."
            ),
            "display_controllers": display or UNAVAILABLE,
        }

    # -- disk --------------------------------------------------------------

    def _docker_root_dir(self) -> str | None:
        """Where the daemon says its data root is, or None with no guess made.

        Asked rather than assumed. A hardcoded `/var/lib/docker` is a claim
        about a host, made by a program that never looked at it — and on this
        machine it is the wrong claim, which is exactly the case that turns
        "protect the system disk" into a policy that protects the wrong disk.
        """
        code, out, _ = self.host.read(
            ["docker", "info", "--format", "{{.DockerRootDir}}"], BUDGET["docker"])
        if code != 0:
            return None
        value = out.strip()
        # Go templates print `<no value>` for a field the daemon did not report.
        # That is a non-empty string, so without this it would be used as a path
        # and `findmnt --target '<no value>'` would fail somewhere further away.
        if not value or value.startswith("<"):
            return None
        # A data root is a path. Anything else means the format string did not
        # resolve to the field it names — a wrong `--format` returns whatever
        # else the template matched, and using that as a directory would put a
        # nonsense role into the report with a confident `state: PRESENT`.
        if not value.startswith("/"):
            return None
        return value

    def _root(self, name: str) -> str | None:
        """One declared root from `paths.py`, or None if it cannot be resolved.

        Imported by path like every other sibling module here: these files are
        shipped as artifacts, not as an installed package, so a bare import
        would resolve only when the caller happened to have set `sys.path`.
        """
        try:
            paths = _load("serveros_runtime_paths", "paths.py")
            return str(paths.Paths().root(name))
        except Exception:
            # PathRefused, or the module itself failing to load. Both mean the
            # same thing to a caller: this role has no path this run.
            return None

    def _resolved_roles(self) -> list[dict]:
        """Every filesystem a resource question is asked about, resolved.

        A role that cannot be resolved is RETURNED with `path: None` and its
        reason, never omitted. Dropping it is how a report comes to say
        "3 filesystems, all healthy" on a host where the fourth was the one
        nobody could read — the count and the silence agree, and both are
        wrong.
        """
        if self._roles_cache is not None:
            return self._roles_cache
        roles = [{"role": role, "path": path, "source": "declared path",
                  "reason": None}
                 for path, role in self.FILESYSTEM_ROLES.items()]
        docker_root = self._docker_root_dir()
        roles.append({
            "role": self.DOCKER_ROLE,
            "path": docker_root,
            "source": "docker info {{.DockerRootDir}}",
            "reason": None if docker_root else
                      "docker info did not report DockerRootDir",
        })
        for role, name in self.SEMANTIC_FILESYSTEM_ROLES.items():
            resolved = self._root(name)
            roles.append({
                "role": role,
                "path": resolved,
                "source": f"paths.Paths().root({name!r})",
                "reason": None if resolved else
                          f"the {name!r} root could not be resolved",
            })
        self._roles_cache = roles
        return roles

    def disk_status(self) -> dict:
        mounts = [self._mount(role["path"], role["role"], reason=role["reason"],
                              source_of_path=role["source"])
                  for role in self._resolved_roles()]
        self._mark_shared(mounts)
        # Everything else that is a real filesystem, excluding pseudo ones.
        code, out, err = self.host.read(
            ["df", "-B1", "-x", "tmpfs", "-x", "devtmpfs", "-x", "squashfs",
             "-x", "overlay", "-x", "iso9660", "--output=source,target,fstype,size,used,avail,pcent,itotal,iused,ipcent"],
            BUDGET["df"],
        )
        # Deduplicated by the mount the role LANDED on, not the path it was
        # asked about: DOCKER_FS asked about `/var/lib/docker` and landing on
        # `/` would otherwise appear a second time in `others` as a filesystem
        # with no role, which is the opposite of what it is.
        covered = {m["path"] for m in mounts if m.get("path")}
        covered |= {m["mount_point"] for m in mounts if m.get("mount_point")}
        others = []
        if code == 0:
            lines = out.strip().splitlines()
            for line in lines[1:]:
                fields = line.split()
                if len(fields) < 10:
                    continue
                target = fields[1]
                if target in covered:
                    continue
                others.append({
                    "source": fields[0], "target": target, "fstype": fields[2],
                    "size_bytes": int(fields[3]), "used_bytes": int(fields[4]),
                    "avail_bytes": int(fields[5]), "used_percent": fields[6],
                    "inodes_total": int(fields[7]), "inodes_used": int(fields[8]),
                    "inodes_percent": fields[9],
                })
        else:
            others = [_unavailable(err.strip()[:200])]
        return {"roles": mounts, "others": others}

    def _mount(self, path: str | None, role: str, *, reason: str | None = None,
               source_of_path: str | None = None) -> dict:
        """One role's filesystem, measured or named as unmeasured.

        `TARGET` from `findmnt` is kept because the path a role is *asked about*
        and the mount it *lands on* are different strings: DOCKER_FS is asked
        about `/var/lib/docker` and lands on `/`. Without that field a reader
        cannot tell "Docker has its own disk" from "Docker is on the root disk",
        which is the distinction §5 exists to make.
        """
        if path is None:
            # Not ABSENT: absent is a measurement, and there is nothing to
            # measure against until the path is known.
            return {"role": role, "path": None, "state": UNAVAILABLE,
                    "reason": reason or "this role has no resolved path",
                    "resolved_from": source_of_path}
        entry: dict = {"role": role, "path": path, "resolved_from": source_of_path}
        if not self.host.exists(path):
            entry["state"] = "ABSENT"
            return entry
        usage = self.host.statvfs(path)
        if usage is None:
            entry["state"] = UNAVAILABLE
            return entry
        used = usage.used
        entry.update({
            "state": "PRESENT",
            "total_bytes": usage.total,
            "used_bytes": used,
            "free_bytes": usage.free,
            "used_percent": round(100.0 * used / usage.total, 1) if usage.total else None,
            # A second exhaustion axis on the same filesystem. A disk with free
            # bytes and no free inodes cannot create a file, and until now the
            # two role mounts — the ones policy actually reads — had no inode
            # figure at all, while the "others" segment did.
            "inodes": self.host.inode_usage(path),
        })
        code, out, _ = self.host.read(
            ["findmnt", "-no", "SOURCE,FSTYPE,TARGET", "--target", path], BUDGET["fast"],
        )
        if code == 0 and out.strip():
            parts = out.split()
            entry["source"] = parts[0]
            entry["fstype"] = parts[1] if len(parts) > 1 else None
            entry["mount_point"] = parts[2] if len(parts) > 2 else None
        # HOME is on ROOT_FS on this host, which is why the root filesystem is
        # a capacity gate and not merely the place the OS lives.
        home = str(_paths.Paths().root("home"))
        if path == "/" and self.host.exists(home):
            entry["note"] = f"HOME ({home}) is on this filesystem"
        return entry

    @staticmethod
    def _mark_shared(mounts: list[dict]) -> None:
        """Name the roles that land on one filesystem, in place.

        "Docker's data root is on ROOT_FS" is a risk statement, and it is only
        visible when the roles are compared to each other rather than read one
        at a time. Two roles sharing a device is not an error — on most hosts it
        is the whole point — but it must be stated, because a threshold applied
        per-role would otherwise be applied twice to one filesystem and read as
        two healthy disks.
        """
        by_device: dict = {}
        for mount in mounts:
            device = mount.get("source") or mount.get("mount_point")
            if mount.get("state") == "PRESENT" and device:
                by_device.setdefault(device, []).append(mount["role"])
        for mount in mounts:
            device = mount.get("source") or mount.get("mount_point")
            peers = [r for r in by_device.get(device, []) if r != mount["role"]]
            if peers:
                mount["shares_filesystem_with"] = sorted(peers)

    def thresholds(self) -> dict:
        """The policy numbers, from the contract that declares them.

        Read through `anomaly.load_thresholds` rather than by opening the YAML
        here. Two readers of one contract are two places for a default to
        drift, and a second copy of a policy in this module would be a second
        authority for it. The import is done at call time, not at module load,
        because `hygiene` loads this module and the daemon loads `hygiene` — a
        module graph that only holds in one import order is one that fails on
        whichever path nobody exercised.
        """
        try:
            anomaly = _load("serveros_runtime_anomaly", "anomaly.py")
            return dict(anomaly.load_thresholds())
        except Exception:
            # An unreadable contract leaves no numbers, and no numbers means no
            # notes — not notes against invented ones. The thresholds reported
            # alongside an empty advisory list say which case this is.
            return {}

    def pressure(self) -> dict:
        """Advisory only. This method returns text for a person to read.

        It is the ONLY place in the module that forms an opinion, and it is
        separated by name so that no caller can mistake the opinion for a
        measured fact — and so that nothing downstream can act on it by
        accident. ServerOS's automatic path is bounded to registered
        disposable resources; root filesystem pressure never reaches it.

        Every note carries the threshold it was decided by, so a reader can
        disagree with the rule instead of only with the number. The three
        comparisons here used to be literals in this function — `85`, `90`,
        and `load1 > cpus` — which is a policy that no reviewer could find and
        no operator could change.
        """
        limits = self.thresholds()
        disks = self.disk_status()
        mem = self._memory()
        notes = []

        disk_high = limits.get("filesystem_percent_high")
        inode_high = limits.get("inode_percent_high")
        for mount in disks["roles"]:
            if mount.get("state") != "PRESENT":
                continue
            where = f"{mount['role']} ({mount['path']})"
            if _at_least(mount.get("used_percent"), disk_high):
                shared = mount.get("shares_filesystem_with")
                notes.append(
                    f"{where} is {mount['used_percent']}% full "
                    f"(threshold {disk_high}%). "
                    + (f"It is the same filesystem as {', '.join(shared)}. "
                       if shared else "")
                    + "ServerOS will not remove anything automatically: "
                      "unknown files are not a category it may touch."
                )
            inodes = mount.get("inodes") or {}
            if _at_least(inodes.get("used_percent"), inode_high):
                notes.append(
                    f"{where} has used {inodes['used_percent']}% of its inodes "
                    f"({inodes['free']} left, threshold {inode_high}%). Free "
                    f"bytes are not the limit here: no free inode means no new "
                    f"file, whatever the byte count says."
                )

        swap = mem.get("swap") or {}
        swap_high = limits.get("swap_percent_high")
        if _at_least(swap.get("used_percent"), swap_high):
            # The watermark opens the question; memory PSI answers it. Reading
            # only the first is what made this note assert pressure on a host
            # that was not under any — see `_swap_note`.
            notes.append(_swap_note(
                swap, swap_high, self.pressure_stall().get("memory"), limits, mem))

        load = self._load()
        cpus = self._cpu().get("logical_cpus")
        per_cpu_high = limits.get("load1_per_cpu_high")
        if cpus and per_cpu_high is not None:
            ratio = round(load.get("load1", 0) / cpus, 2)
            if ratio > per_cpu_high:
                notes.append(
                    f"load1 {load['load1']} over {cpus} logical CPUs is "
                    f"{ratio} per CPU (threshold {per_cpu_high}). Anything "
                    f"ServerOS starts competes with work already here."
                )
        return {
            "advisory_only": True,
            "notes": notes,
            # Reported even when empty: these are the numbers every note above
            # was decided by, and a reader who cannot see them can only accept
            # the opinion or ignore it.
            "thresholds": {k: limits.get(k) for k in (
                "filesystem_percent_high", "inode_percent_high",
                "swap_percent_high", "psi_memory_some_avg60_percent_high",
                "psi_memory_full_avg60_percent_high", "load1_per_cpu_high")},
        }

    # -- processes ---------------------------------------------------------

    def processes_top(self, limit: int = 10, by: str = "cpu") -> dict:
        if by not in ("cpu", "mem"):
            raise ValueError(f"by must be 'cpu' or 'mem', not {by!r}")
        sort_key = "-pcpu" if by == "cpu" else "-pmem"
        code, out, err = self.host.read(
            ["ps", "-eo", "pid,user,pcpu,pmem,rss,etimes,comm", f"--sort={sort_key}"],
            BUDGET["fast"],
        )
        if code != 0:
            return _unavailable(err.strip()[:200])
        rows = []
        for line in out.strip().splitlines()[1:]:
            fields = line.split(None, 6)
            if len(fields) < 7:
                continue
            rows.append({
                "pid": int(fields[0]),
                # Reported so a caller can see whose process it is. This
                # inventory is scoped to the current user; another user's
                # processes are visible on ps and are NOT ServerOS's to touch.
                "user": fields[1],
                "cpu_percent": float(fields[2]),
                "mem_percent": float(fields[3]),
                "rss_bytes": int(fields[4]) * 1024,
                "elapsed_seconds": int(fields[5]),
                "command": fields[6],
            })
            if len(rows) >= limit:
                break
        return {
            "sorted_by": by,
            "rows": rows,
            "scope_note": (
                "long-running and other users' processes appear here. Seeing "
                "a process is not owning it; only registered resources are "
                "managed."
            ),
        }

    # -- systemd user ------------------------------------------------------

    def user_services_list(self) -> dict:
        code, out, err = self.host.read(
            ["systemctl", "--user", "list-units", "--type=service", "--all",
             "--no-pager", "--no-legend", "--plain"],
            BUDGET["docker"],
        )
        if code != 0:
            return _unavailable(err.strip()[:200])
        units = []
        for line in out.splitlines():
            fields = line.split(None, 4)
            if len(fields) < 4:
                continue
            units.append({
                "unit": fields[0],
                "load": fields[1],
                "active": fields[2],
                "sub": fields[3],
                "description": fields[4] if len(fields) > 4 else "",
            })
        code2, out2, _ = self.host.read(
            ["systemctl", "--user", "list-unit-files", "--state=enabled",
             "--no-pager", "--no-legend", "--plain"],
            BUDGET["fast"],
        )
        enabled = [ln.split()[0] for ln in out2.splitlines() if ln.split()]
        code3, out3, _ = self.host.read(
            ["loginctl", "show-user", self._user(), "-p", "Linger"], BUDGET["fast"],
        )
        linger = out3.strip().split("=", 1)[-1] if code3 == 0 else UNAVAILABLE
        return {
            "units": units,
            "enabled_unit_files": enabled,
            # Linger decides whether a user unit survives logout, which is the
            # difference between "always-on" and "on while someone is logged
            # in". It is recorded here because it is a fact the fleet's
            # per-device availability claim depends on.
            "linger": linger,
            "supervisor": "systemd --user",
        }

    def _user(self) -> str:
        code, out, _ = self.host.read(["id", "-un"], BUDGET["fast"])
        return out.strip() if code == 0 else "unknown"

    # -- docker ------------------------------------------------------------

    def docker_summary(self) -> dict:
        code, out, err = self.host.read(
            ["docker", "info", "--format", "{{json .}}"], BUDGET["docker"],
        )
        if code != 0:
            return _unavailable(err.strip()[:200] or "docker is not reachable")
        try:
            info = json.loads(out)
        except json.JSONDecodeError:
            return _unavailable("docker info returned non-JSON")
        keep = (
            "ServerVersion", "Driver", "DockerRootDir", "Containers",
            "ContainersRunning", "ContainersPaused", "ContainersStopped",
            "Images", "NCPU", "MemTotal", "OperatingSystem", "KernelVersion",
            "CgroupVersion", "LiveRestoreEnabled",
        )
        summary = {k: info.get(k) for k in keep}
        summary["_note"] = (
            "counts only. `docker system df` is deliberately NOT run: it was "
            "measured on this host at over 120s, and a hygiene command that "
            "can hang is worse than one that reports less."
        )
        return summary

    def docker_list(self, all_: bool = True) -> dict:
        args = ["docker", "ps", "--format", "{{json .}}"]
        if all_:
            args.insert(2, "-a")
        code, out, err = self.host.read(args, BUDGET["docker"])
        if code != 0:
            return _unavailable(err.strip()[:200] or "docker is not reachable")
        rows = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return {"count": len(rows), "containers": rows}

    def docker_volumes(self) -> dict:
        code, out, err = self.host.read(
            ["docker", "volume", "ls", "--format", "{{json .}}"], BUDGET["docker"],
        )
        if code != 0:
            return _unavailable(err.strip()[:200] or "docker is not reachable")
        rows = [json.loads(ln) for ln in out.splitlines() if ln.strip().startswith("{")]
        return {"count": len(rows), "volumes": rows}

    def _docker_json_lines(self, args: list[str], *, timeout: float | None = None):
        """The shared body of every `--format {{json .}}` read.

        Returns (rows, None) or (None, unavailable-dict). Centralised because
        §27's rule — an unreachable provider is not an empty result — has to
        hold for every one of these, and a rule applied at five call sites is a
        rule that will be missed at the sixth.
        """
        code, out, err = self.host.read(args, BUDGET["docker"] if timeout is None else timeout)
        if code != 0:
            return None, _unavailable(
                err.strip()[:200] or f"docker is not reachable ({' '.join(args[:2])})")
        rows = []
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows, None

    def docker_images(self) -> dict:
        """Every image, with the two facts a governance decision needs.

        `Containers` is how many containers reference the image — an image with
        zero is a candidate and an image with two is not, and that is a fact
        about containers rather than about the image, so it has to be read here
        rather than inferred from an age. `Dangling` is derived from the
        repository name docker itself reports, not from a `<none>` string
        match, because that string is also what an untagged *named* image
        carries.
        """
        rows, problem = self._docker_json_lines(
            ["docker", "images", "--format", "{{json .}}"])
        if problem:
            return problem
        images = []
        dangling = 0
        for row in rows:
            repository = (row.get("Repository") or "").strip()
            tag = (row.get("Tag") or "").strip()
            is_dangling = repository in ("", "<none>") or tag in ("", "<none>")
            dangling += 1 if is_dangling else 0
            images.append({
                "id": row.get("ID"),
                "repository": repository,
                "tag": tag,
                "digest": (row.get("Digest") or "").strip() or None,
                "created_since": row.get("CreatedSince"),
                "size_bytes": _parse_size(row.get("Size")),
                "containers_using": _int_or_none(row.get("Containers")),
                "dangling": is_dangling,
            })
        return {
            "count": len(images),
            "dangling": dangling,
            "containers_using_fewest": min(
                (i["containers_using"] or 0 for i in images), default=None),
            "images": images,
        }

    def docker_stats(self) -> dict:
        """Per-container consumption, right now.

        This is the only live-usage producer in the product, and it is the
        answer to §14's question from the container side: which container is
        actually holding the machine. `docker stats --no-stream` is one read of
        every container's current counters — it is not a 22s call like
        `docker system df`, and it is not a sampler loop.

        A container the daemon did not report is ABSENT from here rather than
        present with zeros. Stopped containers produce no row at all, and a
        zero would read as "running and idle".
        """
        rows, problem = self._docker_json_lines(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"])
        if problem:
            return problem
        containers = []
        for row in rows:
            used, _, limit = (row.get("MemUsage") or "").partition("/")
            containers.append({
                "id": row.get("ID") or row.get("Container"),
                "name": row.get("Name"),
                "cpu_percent": _parse_percent(row.get("CPUPerc")),
                "memory_used_bytes": _parse_size(used),
                "memory_limit_bytes": _parse_size(limit),
                "memory_percent": _parse_percent(row.get("MemPerc")),
                "network_io": (row.get("NetIO") or "").strip() or None,
                "block_io": (row.get("BlockIO") or "").strip() or None,
                "pids": _int_or_none(row.get("PIDs")),
            })
        return {"count": len(containers), "containers": containers}

    def container_logs(self) -> dict:
        """Per-container log file, measured by size and never by content.

        **Size only.** A container log is application output; it can carry
        tokens, cookies and message bodies, and nothing in ServerOS reads it
        for any purpose. The path and the byte count are the whole of what this
        returns — there is no code path from here to the bytes.

        The daemon reports WHERE its log is, not how big it is. On this host
        that path is unreadable from the observing user: Docker's data root is
        `/opt/applications/data/docker` at mode `0710 root:root`, and this
        process runs as `hwz`, which is in group `docker` (111) but not in
        group `root` (0). So every container here is `UNREADABLE` with the
        `OSError` that says so, and **the honest total is unknown rather than
        zero**. `docker system df`'s Containers row is the only number this
        host can produce for the aggregate, and it does not separate logs from
        writable layers.

        A daemon on another driver (`journald`, `syslog`) reports an empty
        `LogPath` because there is no file at all. That is a different fact
        from a file that exists and cannot be opened, and the two are reported
        differently.
        """
        code, out, err = self.host.read(
            ["docker", "ps", "-aq"], BUDGET["docker"])
        if code != 0:
            return _unavailable(err.strip()[:200] or "docker is not reachable")
        ids = [line.strip() for line in out.splitlines() if line.strip()]
        if not ids:
            # An empty list from a reachable daemon IS a fact: no containers.
            return {"state": "PRESENT", "count": 0, "containers": [],
                    "observable": 0, "unobservable": 0, "logs_total_bytes": None}
        # One inspect for every container rather than one per container:
        # 0.06s for 38 here, and the per-container form is what makes an
        # inventory slow enough that somebody lowers its budget and loses a
        # field. `{{.Name}}` carries a leading slash.
        code, out, err = self.host.read(
            ["docker", "inspect", "--format",
             "{{.Id}}|{{.Name}}|{{.HostConfig.LogConfig.Type}}|{{.LogPath}}"] + ids,
            BUDGET["docker"])
        if code != 0:
            return _unavailable(err.strip()[:200] or "docker inspect failed")
        containers = []
        observable = 0
        total = 0
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) < 4:
                continue
            container_id, name, driver, log_path = (
                parts[0].strip(), parts[1].strip().lstrip("/"), parts[2].strip(),
                parts[3].strip())
            entry = {"id": container_id, "name": name, "driver": driver}
            if not log_path:
                entry.update({
                    "log_path": None, "state": "NO_LOG_FILE",
                    "reason": (f"the {driver or 'configured'} log driver writes "
                               f"nowhere on this filesystem, so there is no log "
                               f"file size to report"),
                })
            else:
                entry["log_path"] = log_path
                stat = self.host.path_stat(log_path)
                if stat.get("state") != "PRESENT":
                    entry.update({"state": "UNREADABLE", "size_bytes": None,
                                  "reason": stat.get("reason"),
                                  "unreadable_because": stat.get("state")})
                else:
                    entry.update({"state": "PRESENT",
                                  "size_bytes": stat["size_bytes"],
                                  "modified_at": stat["modified_at"]})
                    observable += 1
                    total += stat["size_bytes"]
            containers.append(entry)
        return {
            "count": len(containers),
            "observable": observable,
            "unobservable": len(containers) - observable,
            # None, not 0, when nothing could be read: a zero total reads as
            # "no logs on this machine", which is a claim nobody measured.
            "logs_total_bytes": total if observable else None,
            "containers": containers,
        }

    def docker_networks(self) -> dict:
        """Networks as their own inventory.

        They were previously visible only as a field inside one container's
        `inspect`, which cannot answer "what networks exist" — a network with
        no containers attached appears nowhere.
        """
        rows, problem = self._docker_json_lines(
            ["docker", "network", "ls", "--format", "{{json .}}"])
        if problem:
            return problem
        return {
            "count": len(rows),
            "networks": [{"id": r.get("ID"), "name": r.get("Name"),
                          "driver": r.get("Driver"), "scope": r.get("Scope")}
                         for r in rows],
        }

    def docker_sizes(self) -> dict:
        """Writable layer and root filesystem size per container.

        This is what makes the writable layer answerable without
        `docker system df`: `docker ps -a --size` costs 0.54s here for 38
        containers against 22s for the daemon-wide accounting, and the writable
        layer is the number that grows — the one nothing was collecting.

        The CLI exposes ONE field, `"77.7MB (virtual 1.28GB)"`, not the Engine
        API's separate `SizeRw` / `SizeRootFs`. Both numbers are in it and the
        parse is exact rather than positional, but a reader looking for
        `SizeRw` in `docker inspect`'s schema would not find it here — the
        names in the output below are the product's, not the daemon's.
        """
        rows, problem = self._docker_json_lines(
            ["docker", "ps", "-a", "--size", "--format", "{{json .}}"])
        if problem:
            return problem
        out = []
        for row in rows:
            rw, root = _parse_ps_size(row.get("Size"))
            out.append({
                "id": row.get("ID"),
                "name": row.get("Names"),
                "state": row.get("State"),
                "size_rw_bytes": rw,
                "size_root_fs_bytes": root,
                # The image layers alone, when both numbers are present. This
                # is the part SHARED with other containers from the same image,
                # and summing it across containers double-counts it. Derived
                # here, once, so no caller has to know that.
                "size_image_layers_bytes": (
                    None if rw is None or root is None else max(root - rw, 0)),
            })
        return {
            "count": len(out),
            "containers": out,
            "note": ("these are the daemon's own rounded figures — SI units to "
                     "three significant digits, so `77.7MB` is not an exact "
                     "byte count. Do not diff two of them and call the result "
                     "a measurement."),
        }

    def docker_space(self) -> dict:
        """`docker system df` — the whole-daemon reclaimable picture.

        **Not part of `all()`, and not called on the daemon tick.** Measured on
        this host at 19.3-22.2s across three runs on 2026-09-14, against 0.09s
        for `docker info` and 2.15s for `docker stats`. Docker accounts for
        every image layer and every build-cache record to produce it, and that
        work is linear in those counts (1513 build-cache records here).

        The module used to claim this command "was measured on this host at
        over 120 seconds — it did not finish inside the shell budget". That was
        true when it was written and is not true now: 22s is slow but it
        finishes. Both facts matter — a command that hangs and a command that
        costs 22s need different handling — and the recorded reason was kept
        instead of the measured one, which is how a stale number becomes a
        design. It is opt-in for the cost, not because it does not work.

        `RECLAIMABLE` is REPORTED, never acted on. §24 and §37 both forbid
        prune; this method has no verb that could reach one.
        """
        code, out, err = self.host.read(
            ["docker", "system", "df", "--format", "{{json .}}"], BUDGET["docker_space"])
        if code != 0:
            return _unavailable(err.strip()[:200] or "docker is not reachable")
        # The non-verbose `--format {{json .}}` form is JSON LINES keyed by
        # `Type` — one row per category — and NOT a nested object. `--verbose`
        # is the nested one, and it is the larger read. `--format` is not
        # supported by this verb on every daemon version and silently yields
        # the plain table there, so the table is the fallback rather than an
        # empty result.
        rows = []
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if not rows:
            rows = _parse_df_table(out)
        if not rows:
            return _unavailable(
                "docker system df returned no parseable row; not reporting "
                "0 bytes reclaimable, which would read as a clean daemon")
        sections = {}
        for row in rows:
            name = _slug(row.get("Type"))
            if not name:
                continue
            sections[name] = {
                "count": _int_or_none(row.get("TotalCount") or row.get("Total")),
                "active": _int_or_none(row.get("Active")),
                "size_bytes": _parse_size(row.get("Size")),
                # "177.7GB (92%)": the percentage is a second reading of the
                # same fact, and re-parsing the pair later is how the two come
                # to disagree. The bytes are kept; the percentage is dropped.
                "reclaimable_bytes": _parse_size(
                    str(row.get("Reclaimable", "")).split("(")[0]),
            }
        total = sum((v.get("reclaimable_bytes") or 0) for v in sections.values())
        return {
            "state": "PRESENT",
            "sections": sections,
            "reclaimable_bytes_total": total,
            "note": ("report only. Nothing here removes anything, and no caller "
                     "of this method may act on `reclaimable_bytes_total`: "
                     "reclaimable is what a prune WOULD free, not what is safe "
                     "to free."),
        }

    # -- everything --------------------------------------------------------

    def all(self) -> dict:
        """Everything a routine observation needs, at a cost a tick can pay.

        Measured here: `docker stats` 2.15s and `docker ps -a --size` 0.54s
        against a 60s tick, so the live-usage and writable-layer reads are in.
        `docker_space` — 22s — is NOT: it is opt-in by name, and the reason is
        its cost rather than its correctness. A reader who wants the
        reclaimable picture asks for it and waits; a daemon that needs to
        finish its tick does not.
        """
        return {
            "host": self.host_summary(),
            "disk": self.disk_status(),
            "disk_io": self.disk_io(),
            "cpu_usage": self.cpu_usage(),
            "pressure_stall": self.pressure_stall(),
            "oom": self.oom_evidence(),
            "docker": {
                "summary": self.docker_summary(),
                "containers": self.docker_list(),
                "volumes": self.docker_volumes(),
                "images": self.docker_images(),
                "networks": self.docker_networks(),
                "sizes": self.docker_sizes(),
                "stats": self.docker_stats(),
                "logs": self.container_logs(),
            },
            "processes": {
                "top_cpu": self.processes_top(by="cpu"),
                "top_memory": self.processes_top(by="mem"),
            },
            "user_services": self.user_services_list(),
            "advisory": self.pressure(),
        }
