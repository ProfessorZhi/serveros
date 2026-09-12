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

**Root pressure is reported, never acted on.** `/` is 47% used and `/home/hwz`
lives on it, so HOME capacity is a real constraint; `/opt/applications` is the
500 GB data disk at 61%. Nothing in this module removes anything, and nothing
downstream of it may either: the automatic path is bounded to resources some
owner registered as disposable, and unknown files are not a category it can
reach. `recommend()` produces text for a person.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
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


registry = _load("serveros_runtime_registry", "registry.py")

RegistryError = registry.RegistryError

# Budgets. Chosen so that a full inventory finishes well inside a person's
# patience during an incident, and so that one slow subsystem degrades one
# field instead of the whole report.
BUDGET = {
    "fast": 8.0,      # /proc reads via small tools
    "docker": 25.0,   # docker ps / inspect
    "df": 10.0,
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

    def exists(self, path: str) -> bool:
        return Path(path).exists()


class Inventory:
    """Machine primitives. Every method returns structured data or a named
    UNAVAILABLE — never a zero standing in for "we did not measure"."""

    # Filesystems are labelled by ROLE, not by device name, because the role
    # is what policy depends on. `/dev/vda2` is not meaningful to a policy
    # that says "protect the system disk"; `ROOT_FS` is.
    FILESYSTEM_ROLES = {
        "/": "ROOT_FS",
        "/opt/applications": "DATA_FS",
    }

    def __init__(self, host=None) -> None:
        self.host = host if host is not None else LiveHost()

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

    def disk_status(self) -> dict:
        mounts = []
        for path, role in self.FILESYSTEM_ROLES.items():
            mounts.append(self._mount(path, role))
        # Everything else that is a real filesystem, excluding pseudo ones.
        code, out, err = self.host.read(
            ["df", "-B1", "-x", "tmpfs", "-x", "devtmpfs", "-x", "squashfs",
             "-x", "overlay", "-x", "iso9660", "--output=source,target,fstype,size,used,avail,pcent,itotal,iused,ipcent"],
            BUDGET["df"],
        )
        others = []
        if code == 0:
            lines = out.strip().splitlines()
            for line in lines[1:]:
                fields = line.split()
                if len(fields) < 10:
                    continue
                target = fields[1]
                if target in self.FILESYSTEM_ROLES:
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

    def _mount(self, path: str, role: str) -> dict:
        if not self.host.exists(path):
            return {"role": role, "path": path, "state": "ABSENT"}
        usage = self.host.statvfs(path)
        if usage is None:
            return {"role": role, "path": path, "state": UNAVAILABLE}
        used = usage.used
        entry = {
            "role": role,
            "path": path,
            "state": "PRESENT",
            "total_bytes": usage.total,
            "used_bytes": used,
            "free_bytes": usage.free,
            "used_percent": round(100.0 * used / usage.total, 1) if usage.total else None,
        }
        code, out, _ = self.host.read(
            ["findmnt", "-no", "SOURCE,FSTYPE", "--target", path], BUDGET["fast"],
        )
        if code == 0 and out.strip():
            parts = out.split()
            entry["source"], entry["fstype"] = parts[0], parts[1] if len(parts) > 1 else None
        # HOME is on ROOT_FS on this host, which is why the root filesystem is
        # a capacity gate and not merely the place the OS lives.
        if path == "/" and self.host.exists("/home/hwz"):
            entry["note"] = "HOME (/home/hwz) is on this filesystem"
        return entry

    def pressure(self) -> dict:
        """Advisory only. This method returns text for a person to read.

        It is the ONLY place in the module that forms an opinion, and it is
        separated by name so that no caller can mistake the opinion for a
        measured fact — and so that nothing downstream can act on it by
        accident. ServerOS's automatic path is bounded to registered
        disposable resources; root filesystem pressure never reaches it.
        """
        disks = self.disk_status()
        mem = self._memory()
        notes = []
        for mount in disks["roles"]:
            if mount.get("state") != "PRESENT":
                continue
            pct = mount.get("used_percent")
            if pct is not None and pct >= 85:
                notes.append(
                    f"{mount['role']} ({mount['path']}) is {pct}% full. "
                    f"ServerOS will not remove anything automatically: "
                    f"unknown files are not a category it may touch."
                )
        swap = mem.get("swap") or {}
        if swap.get("used_percent", 0) >= 90:
            notes.append(
                f"swap is {swap['used_percent']}% used "
                f"({swap['used_bytes']} of {swap['total_bytes']} bytes). "
                f"Real memory pressure; a new persistent process has to "
                f"justify itself."
            )
        load = self._load()
        cpus = self._cpu().get("logical_cpus")
        if cpus and load.get("load1", 0) > cpus:
            notes.append(
                f"load1 {load['load1']} exceeds {cpus} logical CPUs. "
                f"Anything ServerOS starts competes with work already here."
            )
        return {"advisory_only": True, "notes": notes}

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

    # -- everything --------------------------------------------------------

    def all(self) -> dict:
        return {
            "host": self.host_summary(),
            "disk": self.disk_status(),
            "docker": {
                "summary": self.docker_summary(),
                "containers": self.docker_list(),
                "volumes": self.docker_volumes(),
            },
            "processes": {
                "top_cpu": self.processes_top(by="cpu"),
                "top_memory": self.processes_top(by="mem"),
            },
            "user_services": self.user_services_list(),
            "advisory": self.pressure(),
        }
