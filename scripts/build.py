#!/usr/bin/env python3
"""Deterministically build the hwz-server-os runtime artifact from the bundle.

The bundle (`plugin/bundle.yaml`) declares the composition. This script
materializes it into a self-contained runtime artifact:

    serveros bundle definition          (this repository, pinned here)
  + ability-management reusable Skills  (read at an explicit pinned revision)
  + serveros Context Capsule            (this repository)
                  |
                  v
    runtime artifact  ->  runtime-assets/hwz-server-os  ->  Claude / Codex

Two properties matter and are enforced below:

1. Determinism. Identical inputs must produce byte-identical output, so the
   artifact can be verified rather than assumed. Byte-code caches are excluded
   because they embed absolute source paths and interpreter version headers.

2. Pinned source. Reusable Skills are read from an explicit ability-management
   revision via `git`, never from a working tree. A stale or dirty checkout on
   this machine therefore cannot silently downgrade the distribution, and no
   generated artifact ever references this machine's build paths.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guarantee
    sys.stderr.write("ERROR: PyYAML is required to read plugin/bundle.yaml\n")
    raise SystemExit(2)

EXCLUDED_DIRS = {"__pycache__", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise ValueError(
            f"git {' '.join(args)} failed in {repo}: {proc.stderr.decode(errors='replace').strip()}"
        )
    return proc.stdout


# ServerOS Core modules that ship as plain code next to the skills. Each is
# declared in bundle.yaml under its own key; this tuple only fixes the order and
# the set of module names the builder is willing to look for.
#
# `mcp` is here for the same reason as the other three: a delegating CLI must be
# able to load it from the installed bundle instead of from a source checkout
# that moves. It differs in one respect — it is not a decision kernel but an
# entry point, so it additionally gets declared to the runtime as a server.
CORE_MODULES = ("context", "space", "relations", "mcp")


def resolve_ability_rev(repo: Path, rev: str) -> str:
    """Resolve a revision to a full commit SHA, rejecting unknown or ambiguous input."""
    out = _git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").decode().strip()
    if not out:
        raise ValueError(f"cannot resolve ability revision: {rev}")
    return out


def local_skill_files(root: Path) -> list[tuple[Path, Path]]:
    """(absolute source, path relative to the skill directory) for a local skill."""
    if not root.is_dir():
        raise ValueError(f"local skill directory is missing: {root}")
    pairs: list[tuple[Path, Path]] = []
    base = root.resolve()
    for path in sorted(root.rglob("*")):
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.name in EXCLUDED_DIRS or path.suffix in EXCLUDED_SUFFIXES:
            continue
        if path.is_symlink():
            raise ValueError(f"symlinks are not allowed in a local skill: {path}")
        if path.is_file():
            if os.path.commonpath((str(path.resolve()), str(base))) != str(base):
                raise ValueError(f"local skill entry escapes its root: {path}")
            pairs.append((path, path.relative_to(root)))
    return pairs


def ability_skill_files(repo: Path, rev: str, name: str) -> list[tuple[bytes, Path]]:
    """(content, path relative to the skill directory) at a pinned revision.

    Reads objects directly, so the state of any working tree is irrelevant.
    """
    prefix = f"skills/{name}/payload/"
    listing = _git(repo, "ls-tree", "-r", "--name-only", rev, "--", f"skills/{name}/payload")
    if not listing.strip():
        raise ValueError(f"ability-management@{rev[:12]} has no payload for skill: {name}")
    pairs: list[tuple[bytes, Path]] = []
    for entry in sorted(listing.decode().splitlines()):
        if not entry:
            continue
        rel = entry[len(prefix):] if entry.startswith(prefix) else None
        if rel is None:
            raise ValueError(f"unexpected payload entry outside {prefix}: {entry}")
        parts = Path(rel).parts
        if any(part in EXCLUDED_DIRS for part in parts) or Path(rel).suffix in EXCLUDED_SUFFIXES:
            continue
        pairs.append((_git(repo, "show", f"{rev}:{entry}"), Path(rel)))
    return pairs


def claude_plugin_manifest(bundle: dict, version: str, skills: list[str]) -> dict:
    manifest = {
        "name": bundle["name"],
        "version": version,
        "description": bundle["description"],
        "author": {"name": bundle["author"]},
        "skills": [f"./skills/{n}" for n in skills],
        "license": bundle["license"],
    }
    # Named explicitly rather than left to convention. A plugin-root `.mcp.json`
    # is discovered by default, so this field is redundant today — and it is the
    # one line that says, in the manifest itself, that this plugin owns a node
    # surface. A convention is not a declaration.
    if _declared_servers(bundle):
        manifest["mcpServers"] = "./.mcp.json"
    return manifest


def codex_plugin_manifest(bundle: dict, version: str) -> dict:
    manifest = {
        "name": bundle["name"],
        "version": version,
        "description": bundle["description"],
        "author": {"name": bundle["author"]},
        "skills": "./skills/",
        "license": bundle["license"],
        "interface": {
            "displayName": bundle["display_name"],
            "shortDescription": bundle["short_description"],
            "longDescription": bundle["long_description"],
            "developerName": bundle["author"],
            "category": bundle["category"],
            "capabilities": ["Skills", "Agent Engineering Workbench", "Federated governance"],
            "defaultPrompt": list(bundle["default_prompt"]),
            "brandColor": bundle["brand_color"],
        },
    }
    if _declared_servers(bundle):
        manifest["mcpServers"] = "./.codex-mcp.json"
    return manifest


def claude_marketplace_manifest(bundle: dict, version: str) -> dict:
    return {
        "name": bundle["name"],
        "description": f"{bundle['name']} self-hosted marketplace",
        "owner": {"name": bundle["author"]},
        "plugins": [
            {
                "name": bundle["name"],
                "source": ".",
                "version": version,
                "description": bundle["description"],
                "license": bundle["license"],
            }
        ],
    }


def codex_marketplace(bundle: dict, output: Path, marketplace_path: Path) -> dict:
    """Render the local Codex marketplace that points at the generated payload."""
    marketplace_root = marketplace_path.resolve().parent.parent.parent
    relative_payload = Path(os.path.relpath(output.resolve(), marketplace_root))
    if relative_payload.parts[:1] == ("..",):
        raise ValueError("plugin output must be inside the Codex marketplace root")
    return {
        "name": f"{bundle['name']}-local",
        "interface": {"displayName": f"{bundle['display_name']} Local"},
        "plugins": [
            {
                "name": bundle["name"],
                "source": {"source": "local", "path": "./" + relative_payload.as_posix()},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": bundle["category"],
            }
        ],
    }


def _declared_servers(bundle: dict) -> list[dict]:
    module = bundle.get("mcp", {}).get("path")
    servers = bundle.get("mcp", {}).get("servers") or []
    if servers and not module:
        raise ValueError("bundle declares mcp servers but no mcp module path")
    for entry in servers:
        for key in ("name", "command", "entry"):
            if not entry.get(key):
                raise ValueError(f"mcp server entry is missing {key!r}: {entry!r}")
        # The entry is joined onto the module path, so a `..` in it would name a
        # path the artifact does not contain: the server would be declared and
        # unable to start, which reads as a runtime fault rather than a typo.
        parts = Path(entry["entry"]).parts
        if Path(entry["entry"]).is_absolute() or ".." in parts:
            raise ValueError(f"mcp entry must stay inside {module}/: {entry['entry']!r}")
        if not (repo_root() / module / entry["entry"]).is_file():
            raise ValueError(f"mcp entry does not exist: {module}/{entry['entry']}")
    return servers


def claude_mcp_manifest(bundle: dict) -> dict:
    """The plugin-root `.mcp.json`, in the `{"mcpServers": {...}}` shape.

    A plugin-root `.mcp.json` is not the same file as a project `.mcp.json`, but
    the runtime loader accepts the same two encodings, because it starts by
    taking `document.mcpServers || document` and only then iterates entries.
    Both therefore load identically, and every first-party plugin in the
    official marketplace ships the bare map. We emit the wrapped form anyway:

      * it is the documented one, so it is what a reader and `claude plugin
        validate` expect;
      * the bare form silently changes meaning if a server is ever named
        `mcpServers`, and "works until someone picks the wrong name" is not a
        property worth shipping.

    The difference is invisible at runtime, which is exactly why it is written
    down here rather than rediscovered later.
    """
    module = bundle.get("mcp", {}).get("path")
    servers = {}
    for entry in _declared_servers(bundle):
        server = {
            "command": entry["command"],
            "args": [f"${{CLAUDE_PLUGIN_ROOT}}/{module}/{entry['entry']}"],
        }
        if entry.get("env"):
            server["env"] = dict(entry["env"])
        servers[entry["name"]] = server
    return {"mcpServers": servers}


def codex_mcp_manifest(bundle: dict) -> dict:
    """The Codex equivalent.

    Two things differ from the Claude side and both are load-bearing:

      * Codex has no plugin-root placeholder, so `command`/`args` are relative
        and `cwd` is the plugin root.
      * `.codex-mcp.json` is NOT a filename Codex looks for. It appears nowhere
        in the codex binary. It is just a filename one installed plugin happens
        to use, and that plugin only reaches its servers because its manifest
        points at the file explicitly. The manifest below does the same, so the
        name is an arbitrary local choice rather than an assumed convention.

    Field order and the presence of `env` are kept identical to the Claude file
    so that the diff between the two is exactly the two differences above.
    """
    module = bundle.get("mcp", {}).get("path")
    servers = {}
    for entry in _declared_servers(bundle):
        server = {
            "command": entry["command"],
            "args": [f"./{module}/{entry['entry']}"],
        }
        if entry.get("env"):
            server["env"] = dict(entry["env"])
        server["cwd"] = "."
        servers[entry["name"]] = server
    return {"mcpServers": servers}


def build_readme(bundle: dict, version: str, skills: list[str], ability_rev: str) -> str:
    servers = _declared_servers(bundle)
    surface = ""
    if servers:
        surface = (
            "Node surface: " + ", ".join(e["name"] for e in servers) + "\n"
            "(`.mcp.json` for Claude, `.codex-mcp.json` for Codex). A thin stdio adapter over "
            "the local `serverosd` socket, holding no state of its own; when the daemon is not "
            "running it reports that rather than answering with an empty result.\n\n"
        )
    return (
        f"# ProfessorZhi/{bundle['name']}\n\n"
        "Generated by `src/serveros/scripts/build.py`. Do not edit by hand.\n\n"
        f"Bundle definition: `{bundle['name']}` (`plugin/bundle.yaml`).\n\n"
        "Provided skills: " + ", ".join(skills) + "\n\n"
        + surface
        + f"Generated version: `{version}` (cachebuster suffix `{bundle['cachebuster']}`).\n\n"
        f"Reusable skills read from `ability-management@{ability_rev}`. "
        "Canonical identity alias: `ProfessorZhi/workspace-maintainer` is presented as the "
        "`workspace-management` runtime name/path.\n\n"
        "Keep the cachebuster fixed for byte-identical rebuilds; bump it once only when a "
        "new generated payload must invalidate an installed plugin cache.\n"
    )


def plan(bundle_path: Path, ability_repo: Path, ability_rev: str, output: Path, marketplace_path: Path):
    bundle = yaml.safe_load(bundle_path.read_text())
    if bundle.get("schema") != "serveros/bundle.v1":
        raise ValueError(f"unsupported bundle schema: {bundle.get('schema')!r}")
    version = f"{bundle['version']}+{bundle['cachebuster']}"

    local_root = repo_root() / "skills"
    files: list[tuple[bytes, Path]] = []
    skills: list[str] = []
    for entry in bundle["skills"]:
        name = entry["name"]
        source = entry["source"]
        skills.append(name)
        if source == "local":
            for src, rel in local_skill_files(local_root / name):
                files.append((src.read_bytes(), Path("skills") / name / rel))
        elif source == "ability-management":
            for content, rel in ability_skill_files(ability_repo, ability_rev, name):
                files.append((content, Path("skills") / name / rel))
        else:
            raise ValueError(f"unknown skill source for {name}: {source!r}")

    # ServerOS Core modules ship verbatim. Each declares its own path under
    # bundle.yaml, so adding a module is one bundle key plus one directory —
    # never a second vocabulary of module names hardcoded here.
    for module in CORE_MODULES:
        module_rel = bundle.get(module, {}).get("path")
        if not module_rel:
            continue
        if module_rel != module:
            raise ValueError(f"{module}: bundle path must be {module!r}, got {module_rel!r}")
        for src, rel in local_skill_files(repo_root() / module_rel):
            files.append((src.read_bytes(), Path(module_rel) / rel))

    manifests = {
        Path(".claude-plugin/plugin.json"): claude_plugin_manifest(bundle, version, skills),
        Path(".claude-plugin/marketplace.json"): claude_marketplace_manifest(bundle, version),
        Path(".codex-plugin/plugin.json"): codex_plugin_manifest(bundle, version),
    }
    if _declared_servers(bundle):
        # Two files, two shapes. Emitted together so that neither runtime can
        # drift into being the only one this plugin actually reaches.
        manifests[Path(".mcp.json")] = claude_mcp_manifest(bundle)
        manifests[Path(".codex-mcp.json")] = codex_mcp_manifest(bundle)
    readme = build_readme(bundle, version, skills, ability_rev)
    marketplace = codex_marketplace(bundle, output, marketplace_path)
    return files, manifests, readme, marketplace, version


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def build(bundle_path: Path, ability_repo: Path, ability_rev: str, output: Path,
          marketplace_path: Path, write: bool) -> int:
    files, manifests, readme, marketplace, _version = plan(
        bundle_path, ability_repo, ability_rev, output, marketplace_path
    )

    expected_files = {rel for _, rel in files} | set(manifests) | {Path("README.md")}

    if not write:
        # A bare exit code is not actionable. Name every drifted path, so that a
        # red `--check` says what to fix rather than only that something changed.
        drifted: set[str] = set()
        actual = {p.relative_to(output) for p in output.rglob("*") if p.is_file()} if output.exists() else set()
        drifted |= {f"unexpected {rel}" for rel in actual - expected_files}
        drifted |= {f"missing {rel}" for rel in expected_files - actual}
        for content, rel in files:
            if (output / rel).is_file() and (output / rel).read_bytes() != content:
                drifted.add(f"content {rel}")
        for rel, value in manifests.items():
            if (output / rel).is_file() and (output / rel).read_bytes() != _json_bytes(value):
                drifted.add(f"content {rel}")
        if (output / "README.md").is_file() and (output / "README.md").read_text() != readme:
            drifted.add("content README.md")
        if not marketplace_path.is_file():
            drifted.add(f"missing {marketplace_path}")
        elif marketplace_path.read_bytes() != _json_bytes(marketplace):
            drifted.add(f"content {marketplace_path}")
        for reason in sorted(drifted):
            print(f"DRIFT {reason}", file=sys.stderr)
        return int(bool(drifted))

    # The output directory is generated-only: remove everything not in the
    # expected set, then prune directories left empty so no residue of excluded
    # build inputs survives.
    if output.exists():
        for path in sorted((p for p in output.rglob("*") if p.is_file()), reverse=True):
            if path.relative_to(output) not in expected_files:
                path.unlink()
        for path in sorted((p for p in output.rglob("*") if p.is_dir()), reverse=True):
            if not any(path.iterdir()):
                path.rmdir()

    for content, rel in files:
        dst = output / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(content)
        if dst.suffix in (".sh", ".py"):
            dst.chmod(0o755)
    for rel, value in manifests.items():
        dst = output / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(_json_bytes(value))
    (output / "README.md").write_text(readme)
    marketplace_path.parent.mkdir(parents=True, exist_ok=True)
    marketplace_path.write_bytes(_json_bytes(marketplace))
    return 0


def main() -> int:
    root = repo_root()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="report drift without writing")
    ap.add_argument("--write", action="store_true", help="regenerate the artifact")
    ap.add_argument("--bundle", type=Path, default=root / "plugin/bundle.yaml")
    ap.add_argument("--ability-repo", type=Path,
                    default=Path(os.environ.get("SERVEROS_ABILITY_REPO", "")) if os.environ.get("SERVEROS_ABILITY_REPO") else None)
    ap.add_argument("--ability-rev", required=True,
                    help="pinned ability-management revision the reusable Skills are read from")
    # The runtime artifact has exactly one home: the stable distribution root.
    # Requiring an explicit destination keeps this repository a source tree and
    # prevents a second, repo-local plugin payload from appearing alongside it.
    ap.add_argument("--output", type=Path, required=True,
                    help="runtime artifact destination, e.g. "
                         "$DATA_ROOT/runtime-assets/hwz-server-os/current")
    ap.add_argument("--marketplace-path", type=Path, required=True,
                    help="Codex marketplace descriptor, e.g. "
                         "$DATA_ROOT/runtime-assets/hwz-server-os/.agents/plugins/marketplace.json")
    args = ap.parse_args()
    if args.check == args.write:
        ap.error("choose exactly one of --check or --write")
    if args.ability_repo is None:
        ap.error("--ability-repo is required (or set SERVEROS_ABILITY_REPO)")
    try:
        rev = resolve_ability_rev(args.ability_repo, args.ability_rev)
        if rev != args.ability_rev:
            raise ValueError(
                f"--ability-rev must be a full commit SHA; {args.ability_rev} resolves to {rev}"
            )
        return build(args.bundle, args.ability_repo, rev, args.output,
                     args.marketplace_path, args.write)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
