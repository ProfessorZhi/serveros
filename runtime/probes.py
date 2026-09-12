#!/usr/bin/env python3
"""Probes: the part that asks a resource whether it can do its job.

`observe` finds a thing. `probe` asks whether it works. This module is the
second half, and it exists because the first half is where the 2026-09-12
incident went wrong: `endpoint_present: true` was a *true* report from a probe
that measured presence, printed next to a service that could not serve a single
request.

The measurement that shaped this file
-------------------------------------
While building it, this host's endpoint was measured directly:

    $ ss -tanp | grep "pid=$(systemctl --user show -p MainPID --value \
                       desktop-commander-remote.service),"
    ESTAB  10.200.6.77:46762  142.251.33.211:443

    $ getent hosts mcp.desktopcommander.app
    142.251.33.211  ghs.googlehosted.com mcp.desktopcommander.app

So the endpoint holds an ESTABLISHED socket **to its own relay host** — and the
journal shows it opened that socket *before* authorization and keeps it while
sitting in the device-authorization wait:

    20:01:26  ⏳ Connecting to Remote MCP https://mcp.desktopcommander.app
    20:01:32  🔌 Connected to Remote MCP
    20:01:33  ⚠️  Persisted session invalid: Invalid Refresh Token: Already Used
    20:01:33  🔐 Starting device authorization flow...
    20:01:41     - ⏳ Waiting for authorization...      ← still ESTABLISHED here

`transport_connected: true` is therefore a false friend **even when it is
measured properly**, because this resource holds the connection while it is
unauthorized. A socket probe would have reported CONNECTED through the entire
54-minute outage.

Three conclusions, and the third is the one that pays for the module
--------------------------------------------------------------------
1.  A probe must never report a value it did not measure. `UNKNOWN` is a
    result, and it is the correct one when the only probe that could answer
    the question cannot run here.
2.  A probe must say *what it measured* in the record, not just what it
    concluded. `evidence: {type: ...}` is what lets a reader tell a provider
    response from a log line, and `authoritative` follows from it.
3.  Health is decided by the **functional** axis, and the rule table enforces
    it rather than relying on callers to remember: `HEALTHY` requires
    `functional: OK`. A resource that is up and connected but whose functional
    axis is UNKNOWN reports DEGRADED — which is what this endpoint would have
    reported on 2026-09-12, had anything been asked.

A journal line may corroborate a `reason`. It may never decide a state. That
is not a stylistic preference: a regex is a claim about a log format, and the
failure mode is a probe that keeps reporting a reason after the format
changed, silently and indefinitely.
"""
from __future__ import annotations

import importlib.util
import json
import re
import socket
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
health = _load("serveros_runtime_health", "health.py")

RegistryError = registry.RegistryError
Observation = health.Observation
unknown = health.unknown
not_applicable = health.not_applicable


class ProbeUnavailable(RegistryError):
    """The probe cannot run here, and says why rather than guessing.

    Always converted into an UNKNOWN axis with `DECLARED_SURFACE_ABSENT` — the
    point of naming the failure is that it is a gap in this runtime's
    observability, not a statement about the resource.
    """


# ---------------------------------------------------------------------------
# Host access, injectable so a test never has to edit the machine it tests
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 8.0


class LiveHost:
    """Read-only access to this machine. Never mutates anything.

    Every method returns structured data or raises. None of them writes, and
    none of them is given the ability to: a probe that can mutate is a
    reconcile path wearing a probe's name, and the two must stay separable or
    "observe only" stops being a property anything can check.
    """

    name = "live-host"

    def now(self) -> str:
        # Local import keeps the module importable without a clock, which the
        # determinism test relies on.
        import datetime

        return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    # -- systemd -----------------------------------------------------------
    def systemd_user_unit(self, unit: str) -> dict:
        try:
            out = subprocess.run(
                [
                    "systemctl", "--user", "show", unit,
                    "-p", "LoadState", "-p", "ActiveState", "-p", "SubState",
                    "-p", "MainPID", "-p", "NRestarts", "-p", "ExecMainStatus",
                ],
                capture_output=True, text=True, timeout=_DEFAULT_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProbeUnavailable(f"systemctl --user unavailable: {exc}") from exc
        if out.returncode != 0:
            raise ProbeUnavailable(
                f"systemctl --user show {unit} exited {out.returncode}: {out.stderr.strip()[:200]}"
            )
        props = {}
        for line in out.stdout.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                props[k] = v
        if props.get("LoadState") in ("not-found", ""):
            raise ProbeUnavailable(f"{unit} is not a loaded user unit")
        return {
            "unit": unit,
            "load_state": props.get("LoadState"),
            "active_state": props.get("ActiveState"),
            "sub_state": props.get("SubState"),
            "main_pid": int(props.get("MainPID") or 0),
            "n_restarts": int(props.get("NRestarts") or 0),
            "exec_main_status": int(props.get("ExecMainStatus") or 0),
        }

    # -- processes ---------------------------------------------------------
    def process_pids(self, pattern: str) -> list[int]:
        import os

        try:
            out = subprocess.run(
                ["pgrep", "-f", pattern], capture_output=True, text=True,
                timeout=_DEFAULT_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProbeUnavailable(f"pgrep unavailable: {exc}") from exc
        # pgrep exits 1 for "no match", which is a measurement, not a failure.
        if out.returncode not in (0, 1):
            raise ProbeUnavailable(f"pgrep exited {out.returncode}")
        found = [int(p) for p in out.stdout.split() if p.isdigit()]
        # `pgrep -f` matches against full command lines, so a probe whose own
        # argv contains the pattern matches itself. Left in, "is this thing
        # running?" answers yes whenever anyone asks. This host has already
        # paid for that class of mistake once (`pkill -f` matching its own
        # shell and killing the session that ran it).
        self_exempt = {os.getpid(), os.getppid()}
        return [p for p in found if p not in self_exempt]

    def socket_peers(self, pids: list[int]) -> list[dict]:
        """Established TCP peers held by the given pids, via /proc.

        Read from /proc rather than `ss` because `ss`'s `-p` needs the process
        to be ours and prints a human table; /proc is per-process and exact.
        """
        peers: list[dict] = []
        for pid in pids:
            try:
                fds = list(Path(f"/proc/{pid}/fd").iterdir())
            except (OSError, PermissionError):
                continue
            for fd in fds:
                try:
                    target = fd.readlink()
                except OSError:
                    continue
                m = re.match(r"socket:\[(\d+)\]", str(target))
                if not m:
                    continue
                peer = _tcp_peer_for_inode(m.group(1))
                if peer:
                    peers.append({"pid": pid, "inode": m.group(1), **peer})
        return peers

    # -- containers --------------------------------------------------------
    def docker_inspect(self, name: str) -> dict:
        return self._docker_json(["docker", "inspect", name])

    def docker_ps(self, all_: bool = True) -> list[dict]:
        args = ["docker", "ps", "--format", "{{json .}}"]
        if all_:
            args.insert(2, "-a")
        out = self._run(args)
        rows = []
        for line in out.splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def _docker_json(self, args: list[str]) -> dict:
        out = self._run(args)
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise ProbeUnavailable(f"docker returned non-JSON: {exc}") from exc
        if not data:
            raise ProbeUnavailable("docker inspect returned nothing")
        return data[0]

    def _run(self, args: list[str]) -> str:
        try:
            out = subprocess.run(
                args, capture_output=True, text=True, timeout=_DEFAULT_TIMEOUT * 3,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProbeUnavailable(f"{args[0]} unavailable: {exc}") from exc
        if out.returncode != 0:
            raise ProbeUnavailable(
                f"{' '.join(args[:3])} exited {out.returncode}: {out.stderr.strip()[:200]}"
            )
        return out.stdout

    # -- network -----------------------------------------------------------
    def resolve(self, hostname: str) -> list[str]:
        try:
            infos = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
        except OSError as exc:
            raise ProbeUnavailable(f"cannot resolve {hostname}: {exc}") from exc
        return sorted({info[4][0] for info in infos})

    def http_get(self, url: str, timeout: float = _DEFAULT_TIMEOUT) -> dict:
        import urllib.error
        import urllib.request

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "serveros-probe/1"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(4096).decode("utf-8", "replace")
                return {"http_status": resp.status, "body_head": body[:512]}
        except urllib.error.HTTPError as exc:
            # An HTTP error is a measurement: the surface answered.
            return {"http_status": exc.code, "body_head": exc.read(512).decode("utf-8", "replace")}
        except Exception as exc:
            raise ProbeUnavailable(f"no HTTP response from {url}: {type(exc).__name__}") from exc

    def socket_connect(self, path: str, timeout: float = _DEFAULT_TIMEOUT) -> dict:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(path)
            return {"connected": True, "path": path}
        except OSError as exc:
            raise ProbeUnavailable(f"cannot connect to {path}: {exc}") from exc
        finally:
            s.close()

    # -- logs --------------------------------------------------------------
    def journal(self, *, unit: str | None, since: str, grep: str | None = None) -> list[str]:
        args = ["journalctl", "--user", "--no-pager", "-o", "cat", "--since", since]
        if unit:
            args += ["-u", unit]
        if grep:
            args += ["-g", grep]
        try:
            out = subprocess.run(args, capture_output=True, text=True, timeout=_DEFAULT_TIMEOUT * 2)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProbeUnavailable(f"journalctl unavailable: {exc}") from exc
        if out.returncode != 0:
            raise ProbeUnavailable(f"journalctl exited {out.returncode}")
        # The journal legitimately contains whatever the service printed,
        # including the credential-bearing line a previous session leaked at
        # 15:56 on 2026-09-12. Diagnostic output is the last place a secret
        # should surface a second time, so it is scrubbed here rather than
        # trusted to never appear again.
        return [_scrub(line.rstrip("\n")) for line in out.stdout.splitlines()]


_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_OPAQUE = re.compile(r"\b(?:sk|ghp|xox[baprs])[-_][A-Za-z0-9_-]{16,}\b")


def _scrub(line: str) -> str:
    line = _JWT.sub("<redacted-jwt>", line)
    line = _OPAQUE.sub("<redacted-token>", line)
    return line


def _tcp_peer_for_inode(inode: str) -> dict | None:
    """Resolve a socket inode to its peer, via the host's TCP tables."""
    for table, family in (("/proc/net/tcp", "ipv4"), ("/proc/net/tcp6", "ipv6")):
        try:
            text = Path(table).read_text()
        except OSError:
            continue
        for line in text.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 10:
                continue
            if parts[9] != inode:
                continue
            if parts[3] != "01":  # 01 = ESTABLISHED
                continue
            return {
                "state": "ESTABLISHED",
                "family": family,
                "local": _hex_addr(parts[1]),
                "peer": _hex_addr(parts[2]),
            }
    return None


def _hex_addr(value: str) -> str:
    host, _, port = value.partition(":")
    port_i = int(port, 16)
    if len(host) == 8:  # ipv4, little-endian words
        octets = [str(int(host[i : i + 2], 16)) for i in (6, 4, 2, 0)]
        return f"{'.'.join(octets)}:{port_i}"
    return f"[{host}]:{port_i}"


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


class Probe:
    kind = ""

    def __init__(self, spec: dict) -> None:
        self.spec = spec

    @property
    def id(self) -> str:
        return self.spec.get("id") or self.kind

    def run(self, resource, host) -> Observation:  # pragma: no cover - abstract
        raise NotImplementedError

    def _by(self) -> dict:
        return {"type": "probe", "id": self.id}


class SystemdUnitState(Probe):
    """Ask systemd. The supervisor's verdict, not a process-table guess."""

    kind = "systemd-unit-state"

    def run(self, resource, host) -> Observation:
        unit = self.spec.get("unit")
        try:
            info = host.systemd_user_unit(unit)
        except ProbeUnavailable as exc:
            return Observation(
                axis="liveness", state="UNAVAILABLE",
                observed_by=self._by(), observed_at=host.now(),
                evidence={"type": "host-measurement", "why": "probe-unavailable",
                          "detail": str(exc)},
                reason="NOT_OBSERVABLE",
            )
        active = info["active_state"]
        # `activating`/`deactivating` are deliberately not collapsed into
        # RUNNING or STOPPED. A resource mid-transition is not up, and calling
        # it up is how a crash loop reads as a healthy service.
        if active == "active":
            state = "RUNNING"
        elif active == "inactive" or active == "failed":
            state = "STOPPED"
        else:
            state = "UNKNOWN"
        return Observation(
            axis="liveness",
            state=state,
            observed_by=self._by(),
            observed_at=host.now(),
            evidence={
                "type": "host-measurement",
                "systemd_active_state": active,
                "systemd_sub_state": info["sub_state"],
                "n_restarts": info["n_restarts"],
                "main_pid": info["main_pid"],
            },
            reason=None if state == "RUNNING" else "PROCESS_DOWN",
        )


class ProcessExists(Probe):
    kind = "process-exists"

    def run(self, resource, host) -> Observation:
        pattern = self.spec.get("pattern")
        try:
            pids = host.process_pids(pattern)
        except ProbeUnavailable as exc:
            return Observation(
                axis="liveness", state="UNAVAILABLE", observed_by=self._by(),
                observed_at=host.now(),
                evidence={"type": "host-measurement", "detail": str(exc)},
                reason="NOT_OBSERVABLE",
            )
        return Observation(
            axis="liveness",
            state="RUNNING" if pids else "STOPPED",
            observed_by=self._by(),
            observed_at=host.now(),
            evidence={"type": "host-measurement", "match_count": len(pids)},
            reason=None if pids else "PROCESS_DOWN",
        )


class ContainerState(Probe):
    kind = "container-state"

    def run(self, resource, host) -> Observation:
        name = self.spec.get("container")
        try:
            info = host.docker_inspect(name)
        except ProbeUnavailable as exc:
            return Observation(
                axis="liveness", state="UNAVAILABLE", observed_by=self._by(),
                observed_at=host.now(),
                evidence={"type": "host-measurement", "detail": str(exc)},
                reason="NOT_OBSERVABLE",
            )
        state = info.get("State") or {}
        status = state.get("Status")
        return Observation(
            axis="liveness",
            state="RUNNING" if status == "running" else "STOPPED",
            observed_by=self._by(),
            observed_at=host.now(),
            evidence={
                "type": "host-measurement",
                "container_status": status,
                "started_at": state.get("StartedAt"),
                "restart_count": info.get("RestartCount"),
                "oom_killed": state.get("OOMKilled"),
            },
            reason=None if status == "running" else "PROCESS_DOWN",
        )


class ContainerHealthcheck(Probe):
    """The image's own HEALTHCHECK verdict.

    Read from the record rather than re-run, because re-running someone else's
    healthcheck is how a probe acquires side effects. When the image declares
    none, this is NOT_APPLICABLE — not OK.
    """

    kind = "container-healthcheck"

    def run(self, resource, host) -> Observation:
        name = self.spec.get("container")
        try:
            info = host.docker_inspect(name)
        except ProbeUnavailable as exc:
            return Observation(
                axis="functional", state="UNKNOWN", observed_by=self._by(),
                observed_at=host.now(),
                evidence={"type": "none", "detail": str(exc)},
                reason="NOT_OBSERVABLE",
            )
        declared = bool((info.get("Config") or {}).get("Healthcheck"))
        if not declared:
            return not_applicable(
                "functional", why="the image declares no HEALTHCHECK"
            )
        state = info.get("State") or {}
        raw = state.get("Health", {}).get("Status")
        if raw == "healthy":
            value, reason = "OK", None
        elif raw == "unhealthy":
            value, reason = "FAILED", "HEALTHCHECK_FAILING"
        else:
            value, reason = "UNKNOWN", None
        return Observation(
            axis="functional", state=value, observed_by=self._by(),
            observed_at=host.now(),
            evidence={
                "type": "declared-healthcheck",
                "health": raw,
                # The last few probe outputs are kept because a failing
                # healthcheck without its output is a verdict with no reason.
                "last_output": (
                    (state.get("Health") or {}).get("Log") or [{}]
                )[-1].get("Output", "")[:400],
            },
            reason=reason,
        )


class HttpHealth(Probe):
    """A real request to a real endpoint — the preferred functional probe."""

    kind = "http-health"

    def run(self, resource, host) -> Observation:
        url = self.spec["url"]
        expect = self.spec.get("expect_status", 200)
        try:
            result = host.http_get(url)
        except ProbeUnavailable as exc:
            return Observation(
                axis="functional", state="FAILED", observed_by=self._by(),
                observed_at=host.now(),
                evidence={"type": "provider-response", "detail": str(exc),
                          "url": url},
                reason="CHANNEL_DISCONNECTED",
            )
        ok = result["http_status"] == expect
        return Observation(
            axis="functional",
            state="OK" if ok else "FAILED",
            observed_by=self._by(),
            observed_at=host.now(),
            evidence={
                "type": "provider-response",
                "url": url,
                "http_status": result["http_status"],
                "expected": expect,
            },
            reason=None if ok else "HEALTHCHECK_FAILING",
        )


class McpToolsList(Probe):
    """MCP initialize + tools/list. Proves the server can answer an agent.

    Deliberately NOT `process exists`: an MCP server whose transport is up and
    whose handshake fails is exactly the case this probe exists to separate.
    """

    kind = "mcp-tools-list"

    def run(self, resource, host) -> Observation:
        import urllib.error
        import urllib.request

        url = self.spec["url"]
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        ).encode()
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream"},
            )
            with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:
                body = resp.read(65536).decode("utf-8", "replace")
                status = resp.status
        except Exception as exc:
            return Observation(
                axis="functional", state="FAILED", observed_by=self._by(),
                observed_at=host.now(),
                evidence={"type": "provider-response", "url": url,
                          "detail": f"{type(exc).__name__}: {exc}"},
                reason="CHANNEL_DISCONNECTED",
            )
        tools = _mcp_tool_names(body)
        return Observation(
            axis="functional",
            state="OK" if tools else "FAILED",
            observed_by=self._by(),
            observed_at=host.now(),
            evidence={"type": "provider-response", "url": url,
                      "http_status": status, "tool_count": len(tools),
                      "tools": tools[:40]},
            reason=None if tools else "HEALTHCHECK_FAILING",
        )


def _mcp_tool_names(body: str) -> list[str]:
    """Pull tool names out of either a JSON body or an SSE-framed one.

    MCP servers answer over `application/json` or `text/event-stream`; a probe
    that only understood the first would report a working server as failed,
    which is the same class of error as reporting a broken one as fine.
    """
    candidates = [body]
    for line in body.splitlines():
        if line.startswith("data:"):
            candidates.append(line[5:].strip())
    for cand in candidates:
        try:
            doc = json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue
        result = (doc or {}).get("result") or {}
        tools = result.get("tools")
        if isinstance(tools, list):
            return [t.get("name", "?") for t in tools if isinstance(t, dict)]
    return []


class RelaySocket(Probe):
    """Establish a connection state for a resource that dials out.

    Reports the MEASUREMENT, not a conclusion about the session. See the
    module docstring: this host's endpoint holds an ESTABLISHED socket to its
    own relay host while it is unauthorized, so `CONNECTED` here does not mean
    the resource works. It means the channel is open.
    """

    kind = "relay-socket"

    def run(self, resource, host) -> Observation:
        pattern = self.spec.get("pattern")
        peer_host = self.spec.get("peer_host")
        # Resolve the NAME, not a recorded address: an address in the
        # declaration would be a route frozen into a contract, and it goes
        # stale the first time the provider moves. `peer_host` says which
        # service; the resolution says where it is right now.
        try:
            wanted = {addr for addr in host.resolve(peer_host)} if peer_host else set()
        except ProbeUnavailable as exc:
            return Observation(
                axis="transport", state="UNKNOWN", observed_by=self._by(),
                observed_at=host.now(),
                evidence={"type": "none", "why": "peer did not resolve",
                          "peer_host": peer_host, "detail": str(exc)},
                reason="NOT_OBSERVABLE",
            )
        try:
            pids = host.process_pids(pattern)
            peers = host.socket_peers(pids) if pids else []
        except ProbeUnavailable as exc:
            return Observation(
                axis="transport", state="UNKNOWN", observed_by=self._by(),
                observed_at=host.now(),
                evidence={"type": "none", "detail": str(exc)},
                reason="NOT_OBSERVABLE",
            )
        matching = [
            p for p in peers
            if p.get("peer", "").rsplit(":", 1)[0].strip("[]") in wanted
        ]
        # No process at all is DISCONNECTED, not UNKNOWN: the absence of the
        # thing that would hold the channel is a measurement.
        if not pids:
            return Observation(
                axis="transport", state="DISCONNECTED", observed_by=self._by(),
                observed_at=host.now(),
                evidence={"type": "host-measurement", "processes": 0},
                reason="PROCESS_DOWN",
            )
        return Observation(
            axis="transport",
            state="CONNECTED" if matching else "DISCONNECTED",
            observed_by=self._by(),
            observed_at=host.now(),
            evidence={
                "type": "host-measurement",
                "established_to_peer": [p["peer"] for p in matching],
                "other_established": sorted({p["peer"] for p in peers} - {
                    p["peer"] for p in matching}),
                "note": (
                    "an open socket is not a live session — see the module "
                    "docstring. This axis never makes a resource HEALTHY; "
                    "only `functional: OK` does."
                ),
            },
            reason=None if matching else "CHANNEL_DISCONNECTED",
        )


class SocketConnect(Probe):
    """Connect to a local IPC socket and require a real request to succeed."""

    kind = "socket-connect"

    def run(self, resource, host) -> Observation:
        path = self.spec["path"]
        try:
            result = host.socket_connect(path)
        except ProbeUnavailable as exc:
            return Observation(
                axis="transport", state="DISCONNECTED", observed_by=self._by(),
                observed_at=host.now(),
                evidence={"type": "host-measurement", "path": path,
                          "detail": str(exc)},
                reason="CHANNEL_DISCONNECTED",
            )
        return Observation(
            axis="transport", state="CONNECTED", observed_by=self._by(),
            observed_at=host.now(),
            evidence={"type": "host-measurement", "path": path,
                      "connected": result["connected"]},
        )


class RelayPresence(Probe):
    """Ask the provider whether this endpoint is present.

    The only probe that answers the real question for a transport-endpoint,
    and it needs a provider API this runtime does not have — the controller
    lives on the control plane, consumed by external MCP clients, and is not
    callable from here. So it reports UNKNOWN and says why.

    Fabricating OK here would be the original sin; fabricating FAILED would be
    worse, because it would look like evidence.
    """

    kind = "relay-presence"

    def run(self, resource, host) -> Observation:
        if not self.spec.get("provider_api"):
            return Observation(
                axis="functional", state="UNKNOWN", observed_by=self._by(),
                observed_at=None,
                evidence={
                    "type": "none",
                    "why": "no provider API is reachable from this runtime",
                    "detail": (
                        "the controller surface lives on the control plane and "
                        "is consumed by external MCP clients; this node has no "
                        "route to it (fleet availability: "
                        "REMOTE_CONTROLLER_NOT_EXPOSED). Measuring presence "
                        "from here would mean inventing a signal."
                    ),
                },
                reason="DECLARED_SURFACE_ABSENT",
            )
        raise ProbeUnavailable(  # pragma: no cover - no provider API yet
            "a provider_api was configured but no client for it is implemented"
        )


class JournalDiagnostic(Probe):
    """A log line, used to REFINE a reason and never to decide a state.

    The result is reported with `evidence: {type: journal-diagnostic}`, which
    `health.EVIDENCE_STRENGTH` marks non-authoritative. `Observation` refuses
    to attach a non-UNKNOWN state to it, so a caller cannot promote a regex to
    a finding even by accident.
    """

    kind = "journal-diagnostic"

    def run(self, resource, host) -> Observation:
        unit = self.spec.get("unit")
        since = self.spec.get("since", "-30 min")
        patterns = self.spec.get("patterns") or []
        try:
            lines = host.journal(unit=unit, since=since)
        except ProbeUnavailable as exc:
            return Observation(
                axis="reason", state="UNKNOWN", observed_by=self._by(),
                observed_at=host.now(),
                evidence={"type": "none", "detail": str(exc)},
                reason="NOT_OBSERVABLE",
            )
        for entry in patterns:
            import re as _re

            needle = _re.compile(entry["match"])
            hits = [ln for ln in lines if needle.search(ln)]
            if hits:
                return Observation(
                    axis="reason",
                    state="UNKNOWN",
                    observed_by={"type": "diagnostic", "id": self.id},
                    observed_at=host.now(),
                    evidence={
                        "type": "journal-diagnostic",
                        "pattern": entry["match"],
                        "hits": len(hits),
                        "sample": hits[-1][:300],
                    },
                    reason=entry["reason"],
                )
        return Observation(
            axis="reason", state="UNKNOWN",
            observed_by={"type": "diagnostic", "id": self.id},
            observed_at=host.now(),
            evidence={"type": "journal-diagnostic", "hits": 0},
            reason=None,
        )


PROBE_KINDS = {
    cls.kind: cls
    for cls in (
        SystemdUnitState, ProcessExists, ContainerState, ContainerHealthcheck,
        HttpHealth, McpToolsList, RelaySocket, SocketConnect, RelayPresence,
        JournalDiagnostic,
    )
}


# ---------------------------------------------------------------------------
# Running a resource's probes
# ---------------------------------------------------------------------------

_PROBE_ORDER = ("liveness", "transport", "functional", "readiness")


def _axis_default(axis: str, resource) -> Observation:
    """The honest default for an axis no probe produced a value for.

    Whether a probe was declared and failed to answer, or none was declared at
    all, the axis is UNKNOWN — and it is recorded which of the two it was, so
    "we asked and got nothing" stays distinguishable from "we never asked".
    """
    declared = {p.get("axis") for p in resource.probes}
    return unknown(axis, why="NOT_OBSERVED" if axis in declared else "NOT_DECLARED")


def observe(resource, host) -> health.Health:
    """Run every declared probe and assemble the four axes.

    One probe per axis, first declaration wins. Two probes for one axis would
    need a merge rule, and every possible merge rule is a policy nobody wrote
    down — so the registry rejects the ambiguity instead (see `tests/`).
    """
    results: dict[str, Observation] = {}
    reason_observation: Observation | None = None

    for spec in resource.probes:
        cls = PROBE_KINDS.get(spec.get("kind"))
        if cls is None:  # pragma: no cover - registry validation prevents this
            continue
        observation = cls(spec).run(resource, host)
        axis = spec.get("axis")
        if axis == "reason":
            if observation.reason:
                reason_observation = observation
            continue
        if axis in results:
            raise RegistryError(
                f"{resource.id}: two probes answer the {axis!r} axis "
                f"({results[axis].observed_by.get('id')} and {spec.get('id')}). "
                f"Any merge rule would be an unwritten policy; declare one."
            )
        results[axis] = observation

    axes = {a: results.get(a) or _axis_default(a, resource) for a in _PROBE_ORDER}

    # The diagnostic is carried BESIDE the axes, never merged into one.
    #
    # The first version of this merged it into the first axis that had no
    # reason yet, which is wrong in a way worth recording: for this host's
    # endpoint the `functional` axis already carries `DECLARED_SURFACE_ABSENT`
    # ("no probe here can answer that"), and the journal carries
    # `AUTH_FAILURE_SUSPECTED` ("here is a hypothesis"). Those are different
    # claims about different things. Merging them either overwrites the honest
    # "we cannot measure this" with a log line, or discards the only
    # actionable signal on the machine. So both are reported, and the record
    # says which is authoritative.
    diagnostic = None
    if reason_observation is not None:
        diagnostic = {
            "reason": reason_observation.reason,
            "observed_by": dict(reason_observation.observed_by),
            "observed_at": reason_observation.observed_at,
            "evidence": dict(reason_observation.evidence),
            "authoritative": False,
            "note": (
                "corroboration from the journal, not a measurement. It may "
                "route an action to NEEDS_HUMAN; it may never set a state."
            ),
        }

    return health.Health(
        liveness=axes["liveness"],
        transport=axes["transport"],
        functional=axes["functional"],
        readiness=axes["readiness"],
        desired=resource.desired,
        diagnostic=diagnostic,
    )


def probe_one(resource, probe_id: str, host) -> Observation:
    """Run exactly one declared probe. Used by `serverosctl probe <id> --one`."""
    for spec in resource.probes:
        if spec.get("id") == probe_id:
            cls = PROBE_KINDS[spec["kind"]]
            return cls(spec).run(resource, host)
    raise RegistryError(
        f"{resource.id} declares no probe {probe_id!r}. Declared: "
        f"{[p.get('id') for p in resource.probes]}"
    )
