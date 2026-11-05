"""What each device says it runs: the `device-versions.v1` record, read safely.

`distribution/device_kit.py update` (Linux) and `distribution/windows/update.ps1`
(Windows) write one small JSON file after an in-place update:

    serveros_commit      the serveros checkout the device now runs
    core_plugin          {name, version} of the serveros core plugin, or null
    ability_family       {profile, revision} rolled out and read back
    core_lite_collector  the collector's `--version` line, or null
    commander            the commander plugin install id (hub only), or null
    recorded_at          when the update wrote it

This module is the ONE place that reads it, for the two routes that serve it:
the node connector's `GET /v1/device/versions` (a device reached over the node
channel) and the hub's own device (read from this account's state directory).

Two rules, the same ones the Core-Lite route keeps:

* the path is owner-fixed and the read is bounded, no-follow and owner-only
  (`node.connector._read_bounded_no_follow`), so no request can make the
  connector read another file;
* the document is PROJECTED, never forwarded: only the fields above, of the
  types above, leave this module. A record for another device is refused.

A device that has not run an update yet is `NOT_RECORDED`, not an error.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SCHEMA = "serveros/device-versions.v1"
MAX_BYTES = 16 * 1024

_STRING_FIELDS = ("serveros_commit", "core_lite_collector", "commander", "recorded_at")


def default_path() -> Path:
    """Where the update tools write the record for THIS account."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ServerOS" / "state" / "device-versions.json"
    return Path.home() / ".local" / "state" / "serveros" / "device-versions.json"


def _text(value: Any, limit: int = 200) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()[:limit]
    return None


def project(document: Any, device_ref: str) -> dict[str, Any]:
    """The reviewed fields of one record, or a refusal saying why there are none."""
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        return {"state": "UNREADABLE", "reason": f"not a {SCHEMA} document"}
    if document.get("device_ref") != device_ref:
        return {"state": "UNREADABLE",
                "reason": "the record names a different device than the one that served it"}
    versions: dict[str, Any] = {key: _text(document.get(key)) for key in _STRING_FIELDS}
    plugin = document.get("core_plugin")
    versions["core_plugin"] = ({"name": _text(plugin.get("name")),
                                "version": _text(plugin.get("version"))}
                               if isinstance(plugin, dict) else None)
    family = document.get("ability_family")
    versions["ability_family"] = ({"profile": _text(family.get("profile")),
                                   "revision": _text(family.get("revision"))}
                                  if isinstance(family, dict) else None)
    return {"state": "RECORDED", "versions": versions}


def read(path: Path, device_ref: str) -> dict[str, Any]:
    """Read and project the record at an owner-fixed `path`."""
    from .errors import FleetRefusal
    from .node.connector import _read_bounded_no_follow

    try:
        raw = _read_bounded_no_follow(path, MAX_BYTES)
    except FleetRefusal as exc:
        if "no Core snapshot at the configured path" in exc.detail:
            return {"state": "NOT_RECORDED",
                    "reason": "no update has recorded versions on this device yet"}
        # The reader's messages name the KIND of failure, never the path.
        return {"state": "UNREADABLE",
                "reason": exc.detail.replace("Core snapshot", "versions record")}
    try:
        document = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return {"state": "UNREADABLE", "reason": "the versions record is not JSON"}
    return project(document, device_ref)
