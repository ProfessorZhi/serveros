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


SECRET_NAMES = Path("~/.local/state/serveros/secret-names.json")
NAME_RE = __import__("re").compile(r"^[A-Z_][A-Z0-9_]{0,127}$")


def secret_names(path: Path | None = None) -> dict[str, Any]:
    """What the key center delivered to THIS device, by name only — the state
    file `serveros_secret.py sync` writes. Owner, 2026-11-09: the names were
    only visible through the model library, which a hub or a device without
    one cannot answer."""
    target = (path or SECRET_NAMES).expanduser()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "NOT_SYNCED"}
    if not isinstance(data, dict) or data.get("schema") != "serveros/secret-names.v1":
        return {"state": "UNREADABLE"}
    return {"state": "SYNCED",
            "names": [n for n in data.get("names") or [] if isinstance(n, str) and NAME_RE.match(n)],
            "synced_at": _text(data.get("synced_at")), "repo_commit": _text(data.get("repo_commit")),
            "errors": len(data.get("errors") or [])}


def running_code() -> dict[str, Any]:
    """Which code THIS process runs — the connector on a device, Fleet on the hub.

    Owner, 2026-11-09: updates moved the checkout while the Windows connector
    kept running a tree laid on 11-05 and the A6000 one a v1.3.1 worktree, so
    every "updated" device still served old routes. The commit the update
    recorded is not the commit that answers; this says which one does.
    """
    import re
    import subprocess

    root = Path(__file__).resolve().parent.parent
    commit = None
    try:
        done = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=5)
        commit = done.stdout.strip() or None if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        pass
    if commit is None:
        # A tree laid with `git archive` has no .git; the installers name its
        # directory after the commit (runtime\fleet-node\<commit>).
        match = re.fullmatch(r"[0-9a-f]{7,40}", root.name)
        commit = match.group(0) if match else None
    return {"commit": commit, "tree": root.name}


def drift(answer: dict[str, Any]) -> bool | None:
    """True when the code that answers is not the commit the last update recorded."""
    recorded = ((answer.get("versions") or {}).get("serveros_commit") or "")
    running = ((answer.get("running") or {}).get("commit") or "")
    if not recorded or not running:
        return None
    n = min(len(recorded), len(running), 40)
    return recorded[:n] != running[:n]


def read(path: Path, device_ref: str) -> dict[str, Any]:
    answer = _read(path, device_ref)
    answer["secrets"] = secret_names()
    answer["running"] = running_code()
    answer["code_drift"] = drift(answer)
    return answer


def _read(path: Path, device_ref: str) -> dict[str, Any]:
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
