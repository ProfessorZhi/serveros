#!/usr/bin/env python3
"""Generate a Claude Code development plugin from the shared Fleet source.

Two artefacts come out of one source, and they are not the same artefact:

- `integrations/fleet-v1-first-install/` builds the **ChatGPT first-install
  package** — a portable MCP+Skills plugin whose two approved PNGs and whose
  observed endpoint are release inputs, and which is `BLOCKED` until both exist;
- this module builds the **Claude Code development adapter** — unbranded, no
  icon slots, no release archive, loaded in place with `--plugin-dir` for
  verifying the server side while the release stays blocked.

The skills are **not copied into this repository twice**: they are read from
`integrations/fleet-v1-first-install/plugin/skills/` and written into the
generated plugin. The scope vocabulary is imported from the same builder rather
than re-spelled, because two lists of scopes are how the two come to disagree.

What this module never does:

- **no icon and no brand asset.** The two approved PNGs are a release input;
  their absence must not block an unbranded development adapter, and a drawn
  substitute would be a brand asset this tool has no authority to invent;
- **no archive.** The output is a directory Claude Code loads in place. A ZIP is
  the artefact that gets installed, and this is not an installable release;
- **no fabricated endpoint.** `--next-mcp-url` has no default: a placeholder URL
  would be a binding to a machine nobody observed.

The manifest shape and the `.mcp.json` shape follow the Claude Code
documentation for the version installed on this host, verified against
`https://code.claude.com/docs/en/plugins/manifest-reference` and
`https://code.claude.com/docs/en/mcp` rather than recalled.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SHARED_BUILDER = ROOT / "fleet-v1-first-install" / "build_first_install.py"
DEFAULT_SKILLS_SOURCE = ROOT / "fleet-v1-first-install" / "plugin" / "skills"

_spec = importlib.util.spec_from_file_location("fleet_first_install_shared", SHARED_BUILDER)
_builder = importlib.util.module_from_spec(_spec)
sys.modules["fleet_first_install_shared"] = _builder
_spec.loader.exec_module(_builder)

Refused = _builder.Refused
SOURCE_REFUSED = _builder.SOURCE_REFUSED
FLEET_SCOPES = _builder.FLEET_SCOPES

#: Where Claude Code looks for a plugin manifest, and what it names the
#: components it finds beside it.
MANIFEST_DIR = ".claude-plugin"
MANIFEST_NAME = "plugin.json"
MCP_FILE = ".mcp.json"
SKILLS_DIR = "skills"
DEV_MARKER = "DEVELOPMENT_ONLY.md"

#: The documented layout is `.claude-plugin/plugin.json` for the manifest and
#: every component at the plugin root — not inside `.claude-plugin/`.
DEV_PLUGIN_NAME = "serveros-fleet-dev"
DEV_DISPLAY_NAME = "ServerOS Fleet (development)"
DEV_VERSION = "0.0.1"

DEFAULT_SCOPES = ("fleet:read",)


def check_scopes(scopes) -> str:
    """Confine the requested scopes to the six this surface mints.

    The value is a single space-separated string, which is the documented form
    of `oauth.scopes` (RFC 6749 §3.3). Narrowing is the point: pinning a subset
    is how an operator keeps a client to scopes a security review approved, and
    a scope outside the six is not a thing this server can grant, so asking for
    it would fail at the authorization server instead of here.
    """
    if isinstance(scopes, str):
        scopes = scopes.split()
    scopes = list(scopes or ())
    if not scopes:
        raise Refused(SOURCE_REFUSED, "at least one scope must be requested")
    outside = [s for s in scopes if s not in FLEET_SCOPES]
    if outside:
        raise Refused(
            SOURCE_REFUSED,
            f"scope(s) {outside} are not minted by this surface "
            f"(the six are {list(FLEET_SCOPES)}). Asking for a scope the "
            f"authorization server does not advertise fails at the server, and a "
            f"scope no code path honours reads to a client as authority it has.",
        )
    return " ".join(scopes)


def check_endpoint(url: object) -> str:
    """A real absolute HTTPS origin plus `/mcp`. No default, no placeholder."""
    if not isinstance(url, str) or not url.strip():
        raise Refused(
            SOURCE_REFUSED,
            "no --next-mcp-url was supplied. A development adapter still binds a "
            "real ingress; a placeholder URL would be a binding to a machine "
            "nobody observed.",
        )
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise Refused(SOURCE_REFUSED, f"{url!r} is not an absolute https URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise Refused(SOURCE_REFUSED,
                      f"{url!r} carries userinfo, a query or a fragment")
    if parts.path.rstrip("/") != "/mcp":
        raise Refused(SOURCE_REFUSED,
                      f"{url!r} does not name /mcp; the Fleet MCP endpoint is /mcp")
    return url.rstrip("/")


def check_skills_source(source: Path) -> Path:
    """Resolve a Skills source after refusing every symlink on the way in.

    `Path.resolve()` erases the evidence: a source directory that is a symlink,
    or a skill folder that points outside the tree, becomes an ordinary path and
    the copy then reads bytes the source owner never put there. Reject the
    symlink before copying, including a symlink anywhere below the source.
    """
    raw = Path(source)
    absolute = Path(os.path.abspath(raw))
    for part in [absolute, *absolute.parents]:
        if part.is_symlink():
            raise Refused(SOURCE_REFUSED,
                          f"skills source path contains a symlink: {part}")
    resolved = raw.resolve()
    if not resolved.is_dir():
        raise Refused(SOURCE_REFUSED, f"no skills source at {resolved}")
    for root, dirs, files in os.walk(resolved, followlinks=False):
        for name in sorted(dirs):
            path = Path(root) / name
            if path.is_symlink():
                raise Refused(
                    SOURCE_REFUSED,
                    f"skills source contains a symlinked directory: "
                    f"{path.relative_to(resolved)}")
            real = path.resolve()
            if real != resolved and resolved not in real.parents:
                raise Refused(
                    SOURCE_REFUSED,
                    f"skills source entry escapes the source tree: "
                    f"{path.relative_to(resolved)}")
        for name in sorted(files):
            path = Path(root) / name
            if path.is_symlink():
                raise Refused(
                    SOURCE_REFUSED,
                    f"skills source contains a symlinked file: "
                    f"{path.relative_to(resolved)}")
            if not path.is_file():
                raise Refused(
                    SOURCE_REFUSED,
                    f"skills source member is not a regular file: "
                    f"{path.relative_to(resolved)}")
            real = path.resolve()
            if resolved not in real.parents:
                raise Refused(
                    SOURCE_REFUSED,
                    f"skills source entry escapes the source tree: "
                    f"{path.relative_to(resolved)}")
    return resolved


def check_paths(source: Path, out: Path) -> None:
    """Refuse an output at or below the source, on resolved paths.

    The same rule the staging tool and the release builder apply, for the same
    reason: a generated tree written into the source changes what the next run
    reads. Here it would also mean the skills directory contains the plugin
    generated *from* it.
    """
    source = Path(source).resolve()
    out = Path(out).resolve()
    if out == source or source in out.parents:
        raise Refused(SOURCE_REFUSED,
                      f"--out {out} is the skills source {source}, or is inside it. "
                      f"The generated tree would contain the directory it was "
                      f"generated from.")
    if out in source.parents:
        raise Refused(SOURCE_REFUSED,
                      f"--out {out} contains the skills source {source}")


def read_skills(source: Path) -> list[tuple[str, str]]:
    """`skills/<name>/SKILL.md`, immediate children only, from the shared source."""
    source = check_skills_source(source)
    found: list[tuple[str, str]] = []
    for child in sorted(source.iterdir()):
        if not child.is_dir():
            raise Refused(SOURCE_REFUSED, f"{child.name} is not a skill directory")
        skill = child / "SKILL.md"
        if not skill.is_file() or not skill.read_text(encoding="utf-8").strip():
            raise Refused(SOURCE_REFUSED, f"{child.name}/SKILL.md is absent or empty")
        found.append((child.name, skill.read_text(encoding="utf-8")))
    if not found:
        raise Refused(SOURCE_REFUSED, f"{source} holds no skill")
    return found


def check_skill_front_matter(name: str, body: str) -> None:
    """A SKILL.md needs `name` and `description`; the name must match its folder."""
    if not body.startswith("---\n"):
        raise Refused(SOURCE_REFUSED, f"skills/{name}/SKILL.md has no front matter")
    end = body.find("\n---", 4)
    if end < 0:
        raise Refused(SOURCE_REFUSED, f"skills/{name}/SKILL.md front matter is unterminated")
    front = body[4:end]
    keys = {}
    for line in front.splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, v = line.split(":", 1)
            keys[k.strip()] = v.strip()
    for required in ("name", "description"):
        if not keys.get(required):
            raise Refused(SOURCE_REFUSED,
                          f"skills/{name}/SKILL.md front matter has no {required!r}")
    if keys["name"] != name:
        raise Refused(
            SOURCE_REFUSED,
            f"skills/{name}/SKILL.md declares name {keys['name']!r}. Claude Code "
            f"namespaces a skill under its directory, so a front-matter name that "
            f"disagrees with the folder is a skill that loads under a name nobody "
            f"reading the folder would predict.",
        )


def plugin_manifest() -> dict:
    """The `.claude-plugin/plugin.json` for the development adapter.

    No `icon`: the adapter is deliberately unbranded. An unknown top-level key
    is stripped with a warning rather than rejected, so a field this module
    invents would vanish silently — which is why every key here is one the
    manifest reference documents.
    """
    return {
        "$schema": "https://code.claude.com/schemas/plugin.json",
        "name": DEV_PLUGIN_NAME,
        "displayName": DEV_DISPLAY_NAME,
        "version": DEV_VERSION,
        "description": (
            "DEVELOPMENT ONLY — unbranded Claude Code adapter for the ServerOS Fleet "
            "MCP surface. Loads in place with --plugin-dir to verify the server side. "
            "Not the release: no approved icons, no archive, and no plugin id."
        ),
        "author": {"name": "ProfessorZhi"},
        "license": "MIT",
        "homepage": "https://github.com/ProfessorZhi/serveros",
        "repository": "https://github.com/ProfessorZhi/serveros",
        "keywords": ["serveros", "fleet", "mcp", "development"],
    }


def mcp_config(url: str, scopes: str) -> dict:
    """`.mcp.json` at the plugin root for the remote HTTP mode.

    `type` is required: Claude Code reads an entry with a `url` and no `type` as
    a stdio server, which is a configuration error rather than a fallback.
    `oauth.scopes` pins what the authorization flow requests, as one
    space-separated string; it takes precedence over discovery.
    """
    return {
        "mcpServers": {
            "serveros-fleet": {
                "type": "http",
                "url": url,
                "oauth": {"scopes": scopes},
            }
        }
    }


def check_owned_entry(entry: object, config: object) -> tuple[str, str]:
    """Validate the local trusted owner runtime without reading its config.

    The planned entry is `bin/serveros-fleet-owned --stdio --config PATH`. It is
    a local runtime, not a remote OAuth client: the adapter must not invent a
    token, a client id or an OAuth consent. Only the executable and config path
    are checked; the config is never opened here.
    """
    if not isinstance(entry, str) or not entry:
        raise Refused(SOURCE_REFUSED, "the stdio-owner mode needs --owned-entry")
    if not isinstance(config, str) or not config:
        raise Refused(SOURCE_REFUSED, "the stdio-owner mode needs --owned-config")
    entry_path = Path(entry)
    config_path = Path(config)
    if not entry_path.is_absolute() or not config_path.is_absolute():
        raise Refused(SOURCE_REFUSED,
                      "owned entry and config must be absolute paths; a relative "
                      "path would resolve differently on the device")
    if not entry_path.is_file():
        raise Refused(SOURCE_REFUSED, f"owned entry is not a file: {entry_path}")
    if not os.access(entry_path, os.X_OK):
        raise Refused(SOURCE_REFUSED, f"owned entry is not executable: {entry_path}")
    if not config_path.is_file():
        raise Refused(SOURCE_REFUSED, f"owned config is not a file: {config_path}")
    return str(entry_path), str(config_path)


def mcp_config_stdio(entry: str, config: str) -> dict:
    """`.mcp.json` for the local trusted owner runtime, no OAuth fields."""
    return {
        "mcpServers": {
            "serveros-fleet": {
                "type": "stdio",
                "command": entry,
                "args": ["--stdio", "--config", config],
            }
        }
    }


DEV_MARKER_BODY = """# DEVELOPMENT ONLY — this is not the release

This directory was generated by `integrations/fleet-cc-dev-plugin/build_cc_plugin.py`
for one purpose: loading the Fleet MCP surface into a real Claude Code session to
verify the **server side**, while the release stays blocked.

| | this adapter | the release |
|---|---|---|
| icons | none, deliberately unbranded | the two approved PNGs, pinned by SHA-256 |
| output | a directory, loaded with `--plugin-dir` | a ZIP, installed once |
| plugin id | none | assigned by the client's Plugin Creator |
| scopes | the subset named in `.mcp.json`, or none for the local owner stdio mode | authorised by the owner's consent |

The default binding is the remote Fleet ingress. An opt-in `stdio-owner` mode
binds the local trusted `bin/serveros-fleet-owned --stdio --config PATH` entry
instead; it carries no OAuth fields and does not read the config or a credential.

Do not install this, publish it, or describe it as the release. The absence of
the approved brand assets blocks the **release build** and nothing else; it must
not be repaired here.
"""


def generate(*, skills_source: Path, out: Path,
             next_mcp_url: str | None = None,
             scopes=DEFAULT_SCOPES, name: str = DEV_PLUGIN_NAME,
             transport: str = "http", owned_entry: str | None = None,
             owned_config: str | None = None) -> dict:
    """Write the plugin directory once, into an empty location.

    `transport="http"` is the remote Fleet ingress with OAuth scopes.
    `transport="stdio-owner"` binds the local trusted owner runtime and is
    deliberately OAuth-free.
    """
    skills_source = check_skills_source(skills_source)
    out = Path(out).resolve()
    check_paths(skills_source, out)
    transport = str(transport)
    if transport == "http":
        if owned_entry is not None or owned_config is not None:
            raise Refused(SOURCE_REFUSED,
                          "HTTP mode and the local owned entry are different "
                          "bindings; choose one")
        url = check_endpoint(next_mcp_url)
        scope_string = check_scopes(scopes)
        binding = None
    elif transport == "stdio-owner":
        if next_mcp_url is not None:
            raise Refused(SOURCE_REFUSED,
                          "stdio-owner mode is local; it must not also carry a "
                          "remote MCP URL")
        owned_entry, owned_config = check_owned_entry(owned_entry, owned_config)
        url = None
        scope_string = None
        binding = mcp_config_stdio(owned_entry, owned_config)
    else:
        raise Refused(SOURCE_REFUSED,
                      f"transport {transport!r} is not http or stdio-owner")

    skills = read_skills(skills_source)
    for skill_name, body in skills:
        check_skill_front_matter(skill_name, body)
    if name != DEV_PLUGIN_NAME:
        raise Refused(SOURCE_REFUSED,
                      f"this adapter is {DEV_PLUGIN_NAME!r}; a second name would be "
                      f"a second plugin, not a variant of this one")

    if out.exists():
        raise Refused(
            SOURCE_REFUSED,
            f"{out} already exists. A generation writes once into an empty "
            f"directory; remove it deliberately rather than blending two runs.",
        )

    manifest = plugin_manifest()
    (out / MANIFEST_DIR).mkdir(parents=True)
    (out / MANIFEST_DIR / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / MCP_FILE).write_text(
        json.dumps(binding if binding is not None else mcp_config(url, scope_string),
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (out / DEV_MARKER).write_text(DEV_MARKER_BODY, encoding="utf-8")

    written: list[str] = [f"{MANIFEST_DIR}/{MANIFEST_NAME}", MCP_FILE, DEV_MARKER]
    for skill_name, body in skills:
        dest = out / SKILLS_DIR / skill_name / "SKILL.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
        written.append(f"{SKILLS_DIR}/{skill_name}/SKILL.md")

    return {
        "schema": "serveros/fleet-cc-dev-plugin.v1",
        "distribution": "DEVELOPMENT_ONLY_NOT_A_RELEASE",
        "installable": False,
        "name": manifest["name"],
        "version": manifest["version"],
        "transport": transport,
        "mcp_url": url,
        "oauth_scopes": scope_string,
        "owned_entry": owned_entry if transport == "stdio-owner" else None,
        "owned_config": owned_config if transport == "stdio-owner" else None,
        "skills": [s for s, _ in skills],
        "skills_source": str(skills_source.relative_to(ROOT)),
        "members": sorted(written),
        "member_sha256": {
            m: hashlib.sha256((out / m).read_bytes()).hexdigest() for m in sorted(written)
        },
        "creates_plugin": False,
        "uploads": [],
        "installs": [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skills-source", default=str(DEFAULT_SKILLS_SOURCE))
    parser.add_argument("--out", required=True)
    parser.add_argument("--next-mcp-url")
    parser.add_argument("--scopes", default=" ".join(DEFAULT_SCOPES))
    parser.add_argument("--transport", choices=("http", "stdio-owner"),
                        default="http")
    parser.add_argument("--owned-entry")
    parser.add_argument("--owned-config")
    args = parser.parse_args(argv)

    try:
        record = generate(skills_source=Path(args.skills_source), out=Path(args.out),
                          next_mcp_url=args.next_mcp_url, scopes=args.scopes,
                          transport=args.transport, owned_entry=args.owned_entry,
                          owned_config=args.owned_config)
    except Refused as refusal:
        print(f"{refusal.status}: {refusal.detail}", file=sys.stderr)
        return 2

    print(f"{record['name']} v{record['version']} DEVELOPMENT_ONLY")
    print(f"out={args.out} skills={len(record['skills'])} "
          f"transport={record['transport']} mcp_url={record['mcp_url']} "
          f"scopes={record['oauth_scopes']!r}")
    for member in record["members"]:
        print(f"  {member}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
