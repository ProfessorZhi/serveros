#!/usr/bin/env python3
"""Assemble the 9-Skill commander source tree for the existing release builder.

This is **not** a publisher. It copies the canonical first-install tree, adds
the #456 `fleet-batch-commander` Skill, bumps the manifest version, and writes a
source map with every Skill's origin and SHA-256. The only archive writer is
still `build_first_install.py`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CANONICAL_PLUGIN = HERE / "plugin"
BATCH_SKILL = (ROOT / "integrations" / "chatgpt-fleet-next" / "skills" /
               "fleet-batch-commander" / "SKILL.md")
PLUGIN_NAME = "serveros-fleet-ingress"
DISPLAY_NAME = "ServerOS Fleet"


class Refused(Exception):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_tree(path: Path) -> None:
    for entry in sorted(path.rglob("*")):
        if entry.is_symlink():
            raise Refused(f"source tree contains a symlink: {entry}")


def assemble(out: Path, *, version: str = "1.0.3") -> dict:
    out = Path(out).resolve()
    if out.exists():
        raise Refused(f"{out} already exists; choose a fresh source directory")
    if out == CANONICAL_PLUGIN or CANONICAL_PLUGIN in out.parents:
        raise Refused("refusing to assemble inside the canonical plugin tree")
    _check_tree(CANONICAL_PLUGIN)
    shutil.copytree(CANONICAL_PLUGIN, out)
    dest = out / "skills" / "fleet-batch-commander" / "SKILL.md"
    dest.parent.mkdir()
    dest.write_bytes(BATCH_SKILL.read_bytes())

    manifest_path = out / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("name") != PLUGIN_NAME:
        raise Refused(f"canonical name is {manifest.get('name')!r}, not {PLUGIN_NAME!r}")
    interface = (((manifest.get("extensions") or {}).get("com.openai") or {})
                 .get("interface") or {})
    if interface.get("displayName") != DISPLAY_NAME:
        raise Refused("canonical displayName moved")
    manifest["version"] = version
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")

    skills = []
    for path in sorted((out / "skills").glob("*/SKILL.md")):
        name = path.parent.name
        origin = (BATCH_SKILL if name == "fleet-batch-commander"
                  else CANONICAL_PLUGIN / "skills" / name / "SKILL.md")
        skills.append({
            "name": name,
            "origin": str(origin.relative_to(ROOT)),
            "sha256": _sha(path),
            "member": f"skills/{name}/SKILL.md",
        })
    if len(skills) != 9 or "fleet-batch-commander" not in {s["name"] for s in skills}:
        raise Refused(f"expected 9 Skills including fleet-batch-commander, got {len(skills)}")
    return {
        "schema": "serveros/fleet-commander-batch-source.v1",
        "plugin_name": manifest["name"],
        "display_name": interface["displayName"],
        "version": version,
        "source": str(out),
        "skills": skills,
        "skill_count": len(skills),
        "archive_writer": "integrations/fleet-v1-first-install/build_first_install.py",
        "network": False,
        "creator_called": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--version", default="1.0.3")
    parser.add_argument("--map", type=Path)
    args = parser.parse_args(argv)
    try:
        record = assemble(args.out, version=args.version)
    except Refused as refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 2
    if args.map:
        args.map.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
