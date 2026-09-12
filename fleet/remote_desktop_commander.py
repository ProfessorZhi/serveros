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

DeviceRef = transport.DeviceRef
DeviceTransport = transport.DeviceTransport
ControllerNotExposed = transport.ControllerNotExposed

# Where a local controller surface would be declared. Absent means absent:
# the contract does not let us infer a controller from an installed binary.
CONTROLLER_DECLARATION = "~/.serveros/controller.yaml"

# The process name of the endpoint, used only by the diagnostic below.
ENDPOINT_MARKER = "desktop-commander"


def declared_controller(path: Path | None = None) -> dict | None:
    """Return the locally declared controller surface, if one is declared.

    A declaration is a positive statement that something on THIS machine can
    be called. Installing a package, or leaving a daemon running, is not a
    declaration.
    """
    target = Path(os.path.expanduser(CONTROLLER_DECLARATION)) if path is None else Path(path)
    if not target.exists():
        return None
    data = transport.load_yaml(target) or {}
    if data.get("kind") != "remote-desktop-commander":
        return None
    if not data.get("surface"):
        return None
    return data


def diagnose_local_endpoint() -> dict:
    """Report what is observable about the endpoint process on this host.

    Diagnostic only. Its output is deliberately NOT shaped like a controller
    handle, so it cannot be mistaken for one and passed to the adapter. It
    answers "what is running here", not "what can I call".

    Matches on argv contents rather than a process-name pattern, and skips
    this process and its ancestors, so the probe cannot observe itself.
    """
    mine = {os.getpid()}
    pid = os.getpid()
    for _ in range(8):  # walk up a bounded number of ancestors
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            break
        if ppid <= 1:
            break
        mine.add(ppid)
        pid = ppid

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
            if not argv or not any(ENDPOINT_MARKER in a for a in argv):
                continue
            endpoints.append(
                {
                    "pid": int(entry.name),
                    "argv_head": [a for a in argv if ENDPOINT_MARKER in a][:2],
                    "mode": "remote" if "remote" in argv else "local",
                }
            )
    return {
        "endpoint_present": bool(endpoints),
        "endpoints": endpoints,
        "controller_declaration": str(Path(os.path.expanduser(CONTROLLER_DECLARATION))),
        "controller_declared": declared_controller() is not None,
        "note": (
            "An endpoint present with no declared controller surface means the "
            "device may be reachable fleet-wide while this runtime has no route to it."
        ),
    }


class RemoteDesktopCommanderTransport(DeviceTransport):
    """First transport implementation behind the ServerOS semantic boundary.

    Granted no capability levels by default. A transport that starts out able
    to do everything is a transport nobody ever had to make a decision about.
    """

    kind = "remote-desktop-commander"

    def __init__(
        self,
        *,
        controller: dict | None = None,
        declaration_path: Path | None = None,
        granted_levels: tuple[str, ...] = (),
    ) -> None:
        super().__init__(granted_levels=granted_levels)
        self._declared = controller if controller is not None else declared_controller(declaration_path)

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
