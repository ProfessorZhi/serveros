#!/usr/bin/env python3
"""Remote Desktop Commander as a ServerOS transport adapter.

The point of this file is the distinction it refuses to blur:

    an endpoint process is running        ≠  a controller is callable

On this fleet's server node the endpoint (`desktop-commander remote`) has been
up for days and holds an established outbound connection to its relay. It
listens on no local socket. Nothing on this machine can call it. Reaching the
other devices is therefore possible from the fleet plane and impossible from a
local agent runtime — and those two facts must not be recorded as one.

What that means for the adapter
-------------------------------
When no controller surface is exposed, every operation raises
`ControllerNotExposed`. It does not return an empty device list, because an
empty list reads as "the fleet has no devices" — a wrong fact that looks like
a right one. It does not fall back to SSH or WinRM either; a fallback changes
the trust boundary without anyone deciding to change it, which is precisely
the outcome the contract's `no_silent_fallback` exists to forbid.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    """Load a sibling module by path.

    This repo's tests load modules by path rather than by package import, so
    the fleet modules follow the same convention and stay importable without
    an installed package.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


transport = _load("serveros_fleet_transport", "transport.py")
controller = _load("serveros_fleet_controller", "controller.py")

DeviceRef = transport.DeviceRef
DeviceTransport = transport.DeviceTransport
ControllerNotExposed = transport.ControllerNotExposed

# Where a local controller surface would be declared. Absent means absent: the
# contract does not let us infer a controller from an installed binary.
CONTROLLER_DECLARATION = controller.CONTROLLER_DECLARATION

# Re-exported so existing callers keep working; the implementation is
# transport-agnostic and lives with the controller boundary.
declared_controller = controller.declared_controller

# The process name of the endpoint, used only by the diagnostic below.
ENDPOINT_MARKER = "desktop-commander"


def _ancestors() -> set[int]:
    """This process and its ancestors, so a probe cannot observe itself."""
    mine = {os.getpid()}
    pid = os.getpid()
    for _ in range(8):  # a bounded walk; a corrupt tree must not hang a check
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            break
        if ppid <= 1:
            break
        mine.add(ppid)
        pid = ppid
    return mine


def classify_argv(argv: list[str]) -> tuple[bool, str | None]:
    """Whether an argv is an endpoint invocation, and in which role.

    Matching the marker as a substring anywhere in argv is wrong, and wrong in
    the direction that matters: a shell whose *command text* mentions the
    package path matches, and the diagnostic then reports an endpoint process
    that does not exist. That false positive was observed live — an old
    `bash -c "... desktop-commander ..."` from an earlier session was counted
    as a fourth endpoint.

    An endpoint invocation names the binary as an argument of its own: no
    whitespace, and the role comes from the arguments AFTER it, not from
    anywhere in the string.
    """
    for index, arg in enumerate(argv):
        if ENDPOINT_MARKER not in arg or any(ch.isspace() for ch in arg):
            continue
        # `desktop-commander`, the resolved `.bin` shim, or `dist/index.js`
        # reached through the package directory. A shell payload mentioning the
        # path is excluded by the whitespace test above.
        tail = arg.rstrip("/").rsplit("/", 1)[-1]
        if tail not in ("desktop-commander", "index.js"):
            continue
        rest = argv[index + 1:]
        if not rest:
            return True, "local"
        if "remote" in rest:
            return True, "remote"
        return True, "local"
    return False, None


def _listening_sockets(pid: int) -> int:
    """How many LISTEN sockets this process holds.

    This is the measurement that separates the two roles. An endpoint dials
    out; a controller listens. A process can be running for days, healthy and
    connected, while holding zero listening sockets — at which point nothing
    on this machine can call it, however present it looks in `ps`.
    """
    inodes: set[str] = set()
    try:
        for fd in (Path(f"/proc/{pid}/fd")).iterdir():
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith("socket:["):
                inodes.add(target[len("socket:["):-1])
    except OSError:
        return 0  # not ours to inspect; report nothing rather than a guess
    if not inodes:
        return 0

    listening = 0
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            rows = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            parts = row.split()
            if len(parts) < 10 or parts[3] != "0A":  # 0A == TCP_LISTEN
                continue
            if parts[9] in inodes:
                listening += 1
    return listening


def observe_local_endpoint() -> dict:
    """What is observable about the endpoint process on this host.

    Diagnostic only. Its output is deliberately NOT shaped like a controller
    handle, so it cannot be mistaken for one and passed to the adapter. It
    answers "what is running here", not "what can I call".

    Matches on argv contents rather than a process-name pattern, and skips
    this process and its ancestors, so the probe cannot observe itself.
    """
    mine = _ancestors()
    endpoints: list[dict] = []
    proc = Path("/proc")
    if proc.is_dir():
        for entry in sorted(proc.iterdir()):
            if not entry.name.isdigit() or int(entry.name) in mine:
                continue
            try:
                argv = (entry / "cmdline").read_bytes().split(b"\0")
            except OSError:
                continue
            argv = [a.decode(errors="replace") for a in argv if a]
            is_endpoint, mode = classify_argv(argv)
            if not is_endpoint:
                continue
            endpoints.append(
                {
                    "pid": int(entry.name),
                    "argv_head": [a for a in argv if ENDPOINT_MARKER in a and not any(c.isspace() for c in a)][:2],
                    "mode": mode,
                    "listening_sockets": _listening_sockets(int(entry.name)),
                }
            )
    return {
        "endpoint_present": bool(endpoints),
        "endpoints": endpoints,
        "listening_sockets": sum(e["listening_sockets"] for e in endpoints),
        "controller_declaration": str(Path(os.path.expanduser(CONTROLLER_DECLARATION))),
        "controller_declared": declared_controller() is not None,
        "note": (
            "An endpoint present with no declared controller surface means the "
            "device may be reachable fleet-wide while this runtime has no route to it."
        ),
    }


# The richer name is the one the docs and callers use; the shorter form is
# kept because it is what the previous round's tooling called.
diagnose_local_endpoint = observe_local_endpoint


class RemoteDesktopCommanderTransport(DeviceTransport):
    """First transport implementation behind the ServerOS semantic boundary.

    Granted no capability levels by default. A transport that starts out able
    to do everything is a transport nobody ever had to make a decision about.
    """

    kind = "remote-desktop-commander"

    def __init__(
        self,
        *,
        declaration: dict | None = None,
        declaration_path: Path | None = None,
        granted_levels: tuple[str, ...] = (),
        endpoint_present: bool | None = None,
    ) -> None:
        super().__init__(granted_levels=granted_levels)
        self._declared = declaration if declaration is not None else declared_controller(declaration_path)
        # Whether the endpoint process is running is an OBSERVATION about this
        # host, and it is injectable so a test can pin it. Probing /proc from a
        # constructor would make every test's result depend on whatever happens
        # to be running on the machine that ran it.
        self._endpoint_present = endpoint_present

    # -- availability ------------------------------------------------------

    def controller_available(self) -> bool:
        return self._declared is not None

    def availability(self, device_ref: DeviceRef) -> transport.Availability:
        """Presence and control are separate answers.

        `fleet_presence` is left as KNOWN_ONLINE when the fleet plane has told
        us so — including through a caller that learned it elsewhere. Only the
        controller and inventory fields are this runtime's to report, and in
        the common case they are NOT_EXPOSED / UNAVAILABLE.
        """
        if self._declared is None:
            return transport.Availability(
                device_ref=device_ref,
                fleet_presence="KNOWN_ONLINE",
                local_agent_controller="NOT_EXPOSED",
                remote_inventory_from_this_runtime="UNAVAILABLE",
                evidence_source="local_endpoint",
            )
        return transport.Availability(
            device_ref=device_ref,
            fleet_presence="KNOWN_ONLINE",
            local_agent_controller="AVAILABLE",
            remote_inventory_from_this_runtime="AVAILABLE",
            evidence_source="local_endpoint",
        )

    def endpoint_present(self) -> bool:
        """Whether the endpoint role is running on this host.

        Note what this is NOT: `controller_available()`. The endpoint and the
        controller are different roles, shipped by different parties, and the
        entire point of this adapter is that one can be true while the other
        is false.
        """
        if self._endpoint_present is None:
            self._endpoint_present = observe_local_endpoint()["endpoint_present"]
        return self._endpoint_present

    def capability_availability(self, device_ref: DeviceRef) -> transport.CapabilityAvailability:
        """One answer per capability, because one answer is not enough.

        Three distinct situations, three distinct values:

          no declaration          EXTERNAL_ONLY — the surface exists; this
                                  runtime cannot call it. Not "unsupported".
          declared, level granted AVAILABLE     — callable here, now.
          declared, not granted   NOT_GRANTED   — callable surface, withheld
                                  from ServerOS by decision.

        Folding the first into NOT_GRANTED would erase the step that closes
        the gap; folding it into AVAILABLE would be a lie about this runtime.
        """
        if self._declared is None:
            capabilities = tuple(
                (level, "EXTERNAL_ONLY") for level in transport.CAPABILITY_LEVELS
            )
        else:
            granted = set(self.granted_levels)
            capabilities = tuple(
                (level, "AVAILABLE" if level in granted else "NOT_GRANTED")
                for level in transport.CAPABILITY_LEVELS
            )
        return transport.CapabilityAvailability(
            device_ref=device_ref,
            transport_kind=self.kind,
            endpoint="AVAILABLE" if self.endpoint_present() else "ABSENT",
            capabilities=capabilities,
            evidence_source="local_endpoint",
        )

    def external_surface(self) -> dict:
        """The published description of the controller plane, or a refusal.

        Returns a description, never a handle. It exists so that the layer
        above can explain WHERE the capability went without this adapter
        becoming something that talks to the controller itself.
        """
        return controller.external_controller()

    def _require_controller(self) -> dict:
        if self._declared is None:
            raise ControllerNotExposed(
                "no controller surface is declared for this runtime "
                f"({CONTROLLER_DECLARATION}). The endpoint being installed or "
                "running does not by itself expose a callable surface. This is "
                "a controller-integration gap, NOT evidence that a device is "
                "missing, and it is not resolved by switching to another "
                "transport."
            )
        return self._declared

    # -- operations --------------------------------------------------------

    def list_devices(self, **kwargs) -> list[dict]:
        # NOT an empty list on failure. An empty list is a claim about the
        # fleet; the truth here is a claim about this runtime.
        self._require_controller()
        raise NotImplementedError(  # pragma: no cover - requires a live controller
            "controller call surface not implemented; see remaining blockers"
        )

    def inspect_device(self, device_ref: DeviceRef, **kwargs) -> dict:
        self._require_controller()
        self.require("inspect")
        raise NotImplementedError(  # pragma: no cover
            "controller call surface not implemented; see remaining blockers"
        )

    def list_directory(self, device_ref: DeviceRef, path: str, depth: int = 1, **kwargs) -> list[dict]:
        self._require_controller()
        self.require("read")
        raise NotImplementedError(  # pragma: no cover
            "controller call surface not implemented; see remaining blockers"
        )

    def read_file(self, device_ref: DeviceRef, path: str, **kwargs) -> bytes:
        self._require_controller()
        self.require("read")
        raise NotImplementedError(  # pragma: no cover
            "controller call surface not implemented; see remaining blockers"
        )

    def run_command(self, device_ref: DeviceRef, command: str, *, level: str = "execute-safe", **kwargs) -> dict:
        self._require_controller()
        self.require(level)
        raise NotImplementedError(  # pragma: no cover
            "controller call surface not implemented; see remaining blockers"
        )
