#!/usr/bin/env python3
"""Generate the Codex client adapter from the shared Fleet Skills.

Codex 0.160.0 was inspected locally before this file was written. It reads a
plugin under `.codex-plugin/plugin.json`, with `skills` and `mcpServers`
pointing at the plugin-relative directories/files, and installs it from a local
marketplace (`.agents/plugins/marketplace.json`). It does **not** read the
Claude Code `.claude-plugin`/`.mcp.json` layout, so this builder is deliberately
separate from `build_cc_plugin.py`.

The shared source is not duplicated: every `SKILL.md` is read from
`integrations/fleet-v1-first-install/plugin/skills/` and written byte for byte.
The output is an uninstalled, unbranded development directory; it is not the
release and it writes no archive. It never touches a global Codex config or a
credential.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SHARED_SKILLS = ROOT / "fleet-v1-first-install" / "plugin" / "skills"

_spec = importlib.util.spec_from_file_location("fleet_cc_dev_shared", HERE / "build_cc_plugin.py")
_cc = importlib.util.module_from_spec(_spec)
sys.modules["fleet_cc_dev_shared"] = _cc
_spec.loader.exec_module(_cc)

Refused = _cc.Refused
SOURCE_REFUSED = _cc.SOURCE_REFUSED

MANIFEST_DIR = ".codex-plugin"
MANIFEST_NAME = "plugin.json"
MCP_FILE = ".codex-mcp.json"
SKILLS_DIR = "skills"
DEV_MARKER = "DEVELOPMENT_ONLY.md"
DEV_PLUGIN_NAME = "serveros-fleet-codex-dev"
DEV_DISPLAY_NAME = "ServerOS Fleet (Codex development)"
DEV_VERSION = "0.0.1"
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _plugin_manifest(version: str) -> dict:
    return {
        "name": DEV_PLUGIN_NAME,
        "version": version,
        "description": (
            "DEVELOPMENT ONLY — Codex adapter for the ServerOS Fleet MCP surface. "
            "Loads an isolated local plugin that carries the shared Operator Skills "
            "and a Fleet MCP binding. Not the release: no icons, no App/Creator "
            "plugin id, and no archive."
        ),
        "author": {"name": "ProfessorZhi"},
        "license": "MIT",
        "skills": "./skills/",
        "mcpServers": "./.codex-mcp.json",
        "interface": {
            "displayName": DEV_DISPLAY_NAME,
            "shortDescription": "ServerOS Fleet development adapter for Codex.",
            "longDescription": (
                "An isolated Codex development client for the ServerOS Fleet MCP "
                "surface. It teaches discovery, owner-max dispatch/create, async "
                "ACK handling and evidence closure, and binds one MCP entry (remote HTTP or the local owned stdio runtime)."
            ),
            "developerName": "ProfessorZhi",
            "category": "Productivity",
        },
    }


def _mcp_config(url: str) -> dict:
    # Codex 0.160.0's `mcp add --url` writes this exact remote entry. OAuth is
    # discovered by the client; no token, client id or secret belongs here.
    return {"mcpServers": {"serveros-fleet": {"url": url}}}


def _mcp_config_stdio(entry: str, config: str) -> dict:
    return {"mcpServers": {"serveros-fleet": {
        "command": entry, "args": ["--stdio", "--config", config]}}}


def generate(*, skills_source: Path = SHARED_SKILLS, out: Path,
             next_mcp_url: str | None = None, version: str = DEV_VERSION,
             name: str = DEV_PLUGIN_NAME, transport: str = "http",
             owned_entry: str | None = None,
             owned_config: str | None = None) -> dict:
    source = _cc.check_skills_source(skills_source)
    out = Path(out).resolve()
    _cc.check_paths(source, out)
    transport = str(transport)
    if transport == "http":
        if owned_entry is not None or owned_config is not None:
            raise Refused(SOURCE_REFUSED,
                          "HTTP mode and the local owned entry are different "
                          "bindings; choose one")
        url = _cc.check_endpoint(next_mcp_url)
        binding = None
    elif transport == "stdio-owner":
        if next_mcp_url is not None:
            raise Refused(SOURCE_REFUSED,
                          "stdio-owner mode is local; it must not also carry a "
                          "remote MCP URL")
        owned_entry, owned_config = _cc.check_owned_entry(owned_entry, owned_config)
        url = None
        binding = _mcp_config_stdio(owned_entry, owned_config)
    else:
        raise Refused(SOURCE_REFUSED,
                      f"transport {transport!r} is not http or stdio-owner")
    if name != DEV_PLUGIN_NAME:
        raise Refused(SOURCE_REFUSED,
                      f"this adapter is {DEV_PLUGIN_NAME!r}; a second name is a "
                      f"second plugin, not a variant of this one")
    if not isinstance(version, str) or not SEMVER_RE.match(version):
        raise Refused(SOURCE_REFUSED, f"version {version!r} is not MAJOR.MINOR.PATCH")
    skills = _cc.read_skills(source)
    for skill_name, body in skills:
        _cc.check_skill_front_matter(skill_name, body)
    if out.exists():
        raise Refused(SOURCE_REFUSED,
                      f"{out} already exists; generate once into an empty directory")

    manifest = _plugin_manifest(version)
    manifest_dir = out / MANIFEST_DIR
    manifest_dir.mkdir(parents=True)
    (manifest_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (out / MCP_FILE).write_text(
        json.dumps(binding if binding is not None else _mcp_config(url),
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (out / DEV_MARKER).write_text(
        "# DEVELOPMENT ONLY — not the release\n\n"
        "This Codex adapter is generated from the shared Fleet Skills. It has no "
        "brand assets, no App/Creator plugin id and no archive. The remote HTTP "
        "mode keeps OAuth discovery; the `stdio-owner` mode binds the local "
        "trusted owner runtime and carries no OAuth fields. Load it in an isolated "
        "CODEX_HOME; do not install it globally.\n", encoding="utf-8")

    written = [f"{MANIFEST_DIR}/{MANIFEST_NAME}", MCP_FILE, DEV_MARKER]
    for skill_name, body in skills:
        dest = out / SKILLS_DIR / skill_name / "SKILL.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
        written.append(f"{SKILLS_DIR}/{skill_name}/SKILL.md")

    return {
        "schema": "serveros/fleet-codex-plugin.v1",
        "distribution": "DEVELOPMENT_ONLY_NOT_A_RELEASE",
        "installable": False,
        "name": manifest["name"],
        "version": manifest["version"],
        "transport": transport,
        "mcp_url": url,
        "owned_entry": owned_entry if transport == "stdio-owner" else None,
        "owned_config": owned_config if transport == "stdio-owner" else None,
        "skills": [name for name, _ in skills],
        "skills_source": str(source.relative_to(ROOT)),
        "members": sorted(written),
        "member_sha256": {
            member: hashlib.sha256((out / member).read_bytes()).hexdigest()
            for member in sorted(written)
        },
        "creates_plugin": False,
        "uploads": [],
        "installs": [],
        "global_config_written": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skills-source", default=str(SHARED_SKILLS))
    parser.add_argument("--out", required=True)
    parser.add_argument("--next-mcp-url")
    parser.add_argument("--version", default=DEV_VERSION)
    parser.add_argument("--transport", choices=("http", "stdio-owner"),
                        default="http")
    parser.add_argument("--owned-entry")
    parser.add_argument("--owned-config")
    args = parser.parse_args(argv)
    try:
        record = generate(skills_source=Path(args.skills_source), out=Path(args.out),
                          next_mcp_url=args.next_mcp_url, version=args.version,
                          transport=args.transport, owned_entry=args.owned_entry,
                          owned_config=args.owned_config)
    except Refused as refusal:
        print(f"{refusal.code}: {refusal.detail}", file=sys.stderr)
        return 2
    print(f"{record['name']} v{record['version']} DEVELOPMENT_ONLY")
    print(f"out={args.out} skills={len(record['skills'])} "
          f"transport={record['transport']} mcp_url={record['mcp_url']}")
    for member in record["members"]:
        print(f"  {member}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
