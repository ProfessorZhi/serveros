#!/usr/bin/env python3
"""Deterministically zip a portable Agent Plugin folder. No network or secrets."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: build_fleet_plugin_zip.py PLUGIN_DIR")
    root = Path(sys.argv[1]).resolve()
    manifest_path = root / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    name, version = manifest.get("name", ""), manifest.get("version", "")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise SystemExit("invalid plugin name")
    if not re.fullmatch(r"[0-9]+\\.[0-9]+\\.[0-9]+", version):
        raise SystemExit("invalid semver version")
    files = [p for p in root.rglob("*") if p.is_file()]
    if not any(p.name == "SKILL.md" and "skills" in p.relative_to(root).parts for p in files):
        raise SystemExit("plugin has no skill")
    if any(p.is_symlink() for p in root.rglob("*")):
        raise SystemExit("symlinks are not permitted")
    out = Path("dist")
    out.mkdir(exist_ok=True)
    archive = out / f"{name}-v{version}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(files):
            rel = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(rel, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    print(f"{archive} sha256={digest}")


if __name__ == "__main__":
    main()
