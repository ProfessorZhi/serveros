#!/usr/bin/env python3
"""Dry-run the ChatGPT Plugin Creator same-identity update contract.

This tool never calls Creator, never uploads and never writes a plugin. It
checks the local inputs an operator would use for `update_plugin`:

    plugin_id, expected_release_id, archive

The public submission documentation describes a ZIP and a *complete* package;
the Plugin Creator issue additionally reports overlay semantics for omitted
files. This preflight takes the stricter position: a complete ZIP, with every
previous member retained. That is the only position that is correct under both
descriptions.

Sources and the claims they support are recorded in
`docs/research/chatgpt-plugin-update-contract-20261028.md`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_overlay  # noqa: E402

SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
APP_ID_PREFIXES = ("asdk_app_", "connector_", "templated_apps_")


class Refused(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _record(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Refused("PREVIOUS_RECORD_INVALID", str(exc)) from exc
    for field in ("plugin_id", "release_id", "version", "name", "members"):
        if not data.get(field):
            raise Refused("PREVIOUS_RECORD_INVALID", f"missing {field}")
    if not SEMVER.match(str(data["version"])):
        raise Refused("PREVIOUS_RECORD_INVALID", "version is not semver")
    return data


def _members(zf: zipfile.ZipFile) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for info in zf.infolist():
        name = info.filename
        if name.endswith("/"):
            continue
        if name.startswith("/") or "\\" in name:
            raise Refused("UNSAFE_MEMBER", name)
        parts = PurePosixPath(name).parts
        if any(part in ("", ".", "..") for part in parts):
            raise Refused("UNSAFE_MEMBER", name)
        mode = (info.external_attr >> 16) & 0o170000
        if stat.S_ISLNK(mode):
            raise Refused("UNSAFE_MEMBER", f"{name} is a symlink")
        if name in found:
            raise Refused("DUPLICATE_MEMBER", name)
        found[name] = zf.read(info)
    return found


def _semver(value: str) -> tuple[int, int, int]:
    if not SEMVER.match(value):
        raise Refused("VERSION_INVALID", value)
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def _check_icons(members: dict[str, bytes], manifest: dict,
                 tmp: Path) -> list[dict]:
    openai = ((manifest.get("extensions") or {}).get("com.openai") or {})
    reports: list[dict] = []
    for slot in ("composerIcon", "logo"):
        value = openai.get(slot)
        if not value:
            reports.append({"slot": slot, "status": "NOT_DECLARED"})
            continue
        if not isinstance(value, str) or not value.startswith("./"):
            raise Refused("ICON_PATH_INVALID", f"{slot}={value!r}")
        rel = value[2:]
        if rel not in members:
            raise Refused("ICON_MISSING", f"{slot} -> {rel}")
        dest = tmp / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(members[rel])
        report = build_overlay.inspect_icon(
            dest, require_square=(slot == "composerIcon"))
        if report.get("status") == "REFUSED":
            raise Refused("ICON_INVALID", f"{slot}: {report.get('reason')}")
        reports.append({"slot": slot, "status": report.get("status"),
                        "format": report.get("format"),
                        "width": report.get("width"),
                        "height": report.get("height"),
                        "bytes": report.get("bytes"),
                        "renders_as": None})
    return reports


def preflight(previous_path: Path, archive: Path, plugin_id: str,
              expected_release_id: str) -> dict:
    previous = _record(previous_path)
    if plugin_id.startswith(APP_ID_PREFIXES):
        raise Refused("APP_ID_NOT_PLUGIN_ID",
                      f"{plugin_id!r} looks like an app ID; use the exact "
                      f"backend plugin_id from Creator")
    if plugin_id != previous["plugin_id"]:
        raise Refused("PLUGIN_ID_MISMATCH",
                      f"{plugin_id!r} != {previous['plugin_id']!r}")
    if not expected_release_id or expected_release_id != previous["release_id"]:
        raise Refused("STALE_EXPECTED_RELEASE_ID",
                      f"{expected_release_id!r} != {previous['release_id']!r}")
    if archive.is_symlink() or not archive.is_file():
        raise Refused("ARCHIVE_INVALID", f"{archive} is not a regular file")
    if archive.suffix.lower() != ".zip" or not zipfile.is_zipfile(archive):
        raise Refused("UNSUPPORTED_ARCHIVE_FORMAT",
                      "public evidence describes ZIP only; tar.gz is not "
                      "established for update_plugin")
    with zipfile.ZipFile(archive) as zf:
        members = _members(zf)
    if "plugin.json" not in members:
        raise Refused("MANIFEST_MISSING", "plugin.json")
    try:
        manifest = json.loads(members["plugin.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Refused("MANIFEST_INVALID", str(exc)) from exc
    if manifest.get("name") != previous["name"]:
        raise Refused("NAME_MISMATCH",
                      f"{manifest.get('name')!r} != {previous['name']!r}")
    version = str(manifest.get("version") or "")
    if _semver(version) <= _semver(str(previous["version"])):
        raise Refused("VERSION_NOT_ADVANCING",
                      f"{version} is not above {previous['version']}")
    missing = sorted(set(previous["members"]) - set(members))
    if missing:
        raise Refused("OMITTED_MEMBER",
                      f"complete ZIP required; missing {missing}")
    app_backed = ".app.json" in members
    with tempfile.TemporaryDirectory() as tmp:
        icons = _check_icons(members, manifest, Path(tmp))
    content = hashlib.sha256()
    for name in sorted(members):
        content.update(name.encode())
        content.update(b"\0")
        content.update(members[name])
        content.update(b"\0")
    return {
        "schema": "serveros/chatgpt-plugin-update-preflight.v1",
        "verdict": "PASS",
        "plugin_id": plugin_id,
        "previous_release_id": previous["release_id"],
        "expected_release_id": expected_release_id,
        "previous_version": previous["version"],
        "next_version": version,
        "archive": str(archive),
        "archive_sha256": _sha(archive.read_bytes()),
        "content_sha256": content.hexdigest(),
        "members": sorted(members),
        "app_backed": app_backed,
        "presentation_status": ("APP_BACKED_PRESENTATION_UNKNOWN"
                                if app_backed else "PORTABLE_MANIFEST"),
        "icons": icons,
        "creator_called": False,
        "uploaded": False,
        "production_verified": False,
        "checks": [
            "exact plugin_id", "expected_release_id guard",
            "ZIP only", "complete previous member set",
            "advancing semantic version", "root manifest name",
            "declared icon files inspected (rendering not asserted)",
        ],
        "note": ("Dry-run only. The preflight does not prove Creator accepts "
                 "this package, and it does not prove an App-backed avatar or "
                 "display name changes."),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--previous", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--expected-release-id", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        result = preflight(args.previous, args.archive, args.plugin_id,
                           args.expected_release_id)
    except Refused as refusal:
        print(f"BLOCKED {refusal.code}: {refusal.detail}", file=sys.stderr)
        return 2
    if args.out:
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
