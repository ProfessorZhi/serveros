#!/usr/bin/env python3
"""Generate the Claude Code adapter for the **V1 release** plugin package.

Why this file exists, next to `build_cc_plugin.py` rather than inside it:

- `build_cc_plugin.py` builds a deliberately **unbranded development** adapter.
  Two of its guarantees are "no icon" and "not a release", and folding a branded
  mode into it would make both of those conditional — the kind of conditional
  that later reads as a bug in whichever mode is not being exercised.
- This module builds the **release-shaped** adapter: the same skills, the same
  observed ingress, the two brand assets the lock pins, all six scopes, and the
  release package's own name and version. It is what a person installs into
  Claude Code when the release package is what they mean.

What it does **not** do: it is not the release archive. The first-install ZIP
from `integrations/fleet-v1-first-install/` is the artefact a portable client
registers; this directory exists because Claude Code's manifest layout is
`.claude-plugin/plugin.json` plus `.mcp.json`, which is a different shape from
the agent-plugins.org package. Both are generated from one source and one lock.

The assets are copied, never generated here: `check_assets` reads the shared
lock and refuses absent or mismatched bytes before anything is written.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SHARED = ROOT / "fleet-v1-first-install"
RELEASE_PLUGIN = SHARED / "plugin"
SHARED_LOCK = SHARED / "brand-assets.lock.json"

#: The development builder already loads the shared first-install builder and
#: keeps it as `_builder`. Loading that file a second time would create a second
#: `Refused` class, and `except Refused` would then miss half the refusals this
#: module is supposed to report. So the shared module is taken from `_cc`.
_cc_spec = importlib.util.spec_from_file_location(
    "fleet_cc_dev_plugin_builder", HERE / "build_cc_plugin.py")
_cc = importlib.util.module_from_spec(_cc_spec)
sys.modules["fleet_cc_dev_plugin_builder"] = _cc
_cc_spec.loader.exec_module(_cc)

_build = _cc._builder
Refused = _build.Refused
SOURCE_REFUSED = _build.SOURCE_REFUSED
MISSING_BRAND_ASSET = _build.MISSING_BRAND_ASSET
BRAND_ASSET_MISMATCH = _build.BRAND_ASSET_MISMATCH

#: Six scopes, not the development adapter's one-scope default: the release
#: package declares all of them, and consent is still the owner's choice.
RELEASE_SCOPES = tuple(_build.FLEET_SCOPES)

#: Claude Code's OAuth loopback. Pinned because an ephemeral port would make
#: every re-consent a different redirect URI, and the authorization server
#: matches the URI it issued the code to.
CALLBACK_PORT = 64648

MCP_FILE = ".mcp.json"
MANIFEST_DIR = ".claude-plugin"
MANIFEST_NAME = "plugin.json"

#: The directory review asks every plugin that reaches a remote MCP server for a
#: privacy policy. The notice ships inside the package (`PRIVACY.md`), and this
#: is its durable address on `main`. Until the branch carrying it is merged that
#: URL is not yet live; a stale-but-honest pointer is reported rather than a
#: policy invented at a URL that never will be.
PRIVACY_POLICY_URL = (
    "https://github.com/ProfessorZhi/serveros/blob/main/"
    "integrations/fleet-v1-first-install/plugin/PRIVACY.md"
)


def release_manifest() -> dict:
    """The agent-plugins.org manifest this adapter is the Claude Code shape of."""
    path = RELEASE_PLUGIN / "plugin.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for key in ("name", "version", "description"):
        if not manifest.get(key):
            raise Refused(SOURCE_REFUSED, f"{path} has no {key!r}")
    return manifest


def cc_manifest(release: dict, icon_path: str) -> dict:
    """`.claude-plugin/plugin.json`.

    Every key is documented for Claude Code. `icon` is the directory-listing
    field: Claude Code ignores it at load time, and the plugin directory reads
    it, which is exactly where a brand slot belongs. It points at the same
    composer icon the portable package declares.

    `privacyPolicyUrl` is required by the directory review for any plugin that
    reaches a remote MCP server, which this one does. The notice itself is in
    the package; the URL is where the directory can read it.
    """
    interface = (release.get("extensions", {})
                 .get("com.openai", {}).get("interface", {}))
    return {
        "$schema": "https://code.claude.com/schemas/plugin.json",
        "name": release["name"],
        "displayName": interface.get("displayName") or release["name"],
        "version": release["version"],
        "description": release["description"],
        "author": release.get("author", {"name": "ProfessorZhi"}),
        "license": release.get("license", "MIT"),
        "homepage": release.get("homepage"),
        "repository": release.get("repository"),
        "keywords": release.get("keywords", []),
        "icon": "./" + icon_path,
        "privacyPolicyUrl": PRIVACY_POLICY_URL,
    }


def mcp_config(url: str, scopes: str) -> dict:
    """`.mcp.json` at the plugin root.

    `type: http` is Claude Code's spelling for a streamable-HTTP server; the
    portable package says `streamable-http` because that is the schema it is
    written against. The URL and the scope string are identical in both.
    """
    return {
        "mcpServers": {
            "serveros-fleet": {
                "type": "http",
                "url": url,
                "oauth": {"scopes": scopes, "callbackPort": CALLBACK_PORT},
            }
        }
    }


def generate(*, out: Path, next_mcp_url: str, scopes=RELEASE_SCOPES) -> dict:
    out = Path(out).resolve()
    _cc.check_paths(RELEASE_PLUGIN / "skills", out)
    url = _cc.check_endpoint(next_mcp_url)
    scope_string = _cc.check_scopes(scopes)
    release = release_manifest()
    lock = json.loads(SHARED_LOCK.read_text(encoding="utf-8"))
    assets = _build.check_assets(RELEASE_PLUGIN, lock)

    skills = _cc.read_skills(RELEASE_PLUGIN / "skills")
    for name, body in skills:
        _cc.check_skill_front_matter(name, body)

    if out.exists():
        raise Refused(
            SOURCE_REFUSED,
            f"{out} already exists. A generation writes once into an empty "
            f"directory; remove it deliberately rather than blending two runs.",
        )

    icon_slot = next(s for s in lock["slots"] if s["slot"] == "composerIcon")
    (out / MANIFEST_DIR).mkdir(parents=True)
    (out / MANIFEST_DIR / MANIFEST_NAME).write_text(
        json.dumps(cc_manifest(release, icon_slot["shipped_path"]),
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (out / MCP_FILE).write_text(
        json.dumps(mcp_config(url, scope_string), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")

    written = [f"{MANIFEST_DIR}/{MANIFEST_NAME}", MCP_FILE]
    for rel in ("README.md", "PRIVACY.md"):
        shutil.copyfile(RELEASE_PLUGIN / rel, out / rel)
        written.append(rel)
    for slot in lock["slots"]:
        rel = slot["shipped_path"]
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(RELEASE_PLUGIN / rel, dest)
        written.append(rel)
    for name, body in skills:
        rel = f"skills/{name}/SKILL.md"
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
        written.append(rel)

    return {
        "schema": "serveros/fleet-cc-release-adapter.v1",
        "name": release["name"],
        "version": release["version"],
        "mcp_url": url,
        "oauth_scopes": scope_string,
        "callback_port": CALLBACK_PORT,
        "assets": assets,
        "skills": [name for name, _ in skills],
        "members": sorted(written),
        "member_sha256": {
            rel: hashlib.sha256((out / rel).read_bytes()).hexdigest()
            for rel in sorted(written)
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--next-mcp-url", required=True)
    parser.add_argument("--scopes", default=" ".join(RELEASE_SCOPES))
    args = parser.parse_args(argv)
    try:
        record = generate(out=Path(args.out), next_mcp_url=args.next_mcp_url,
                          scopes=args.scopes)
    except Refused as refusal:
        print(f"{refusal.status}: {refusal.detail}", file=sys.stderr)
        return 2
    print(json.dumps(record, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
