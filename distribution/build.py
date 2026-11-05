#!/usr/bin/env python3
"""Build, check, package and install the ServerOS Fleet client plugins.

One shared source, three client adapters, one version (`channels.yaml`):

    python3 distribution/build.py build    [--client all|claude-code|codex|openai]
    python3 distribution/build.py check    [--client ...]   # CI: committed == regenerated
    python3 distribution/build.py zip      --client openai --out DIR
    python3 distribution/build.py install  --client claude-code

Every client tree is generated from the shared Skills, assets and manifest under
`integrations/fleet-v1-first-install/plugin/` and committed, so a reviewer sees
exactly what each client receives. Nothing here runs a Fleet server, talks to a
network or touches a credential; `install` only calls the local `claude` CLI.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CHANNELS = HERE / "channels.yaml"
CLIENTS = ("claude-code", "codex", "openai", "mcp")
ZIP_TIME = (2026, 1, 1, 0, 0, 0)          # fixed, so the archive is reproducible


class DistributionError(RuntimeError):
    pass


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def channels() -> dict:
    doc = yaml.safe_load(CHANNELS.read_text(encoding="utf-8"))
    if doc.get("schema") != "serveros/fleet-distribution.v1":
        raise DistributionError("channels.yaml: unknown schema")
    shared = json.loads((ROOT / doc["version_from"]).read_text(encoding="utf-8"))
    doc["version"] = shared["version"]
    doc["shared_manifest"] = shared
    cc = _load("fleet_distribution_cc_checks",
               ROOT / "integrations/fleet-cc-dev-plugin/build_cc_plugin.py")
    doc["server"]["mcp_url"] = cc.check_endpoint(doc["server"]["mcp_url"])
    return doc


def _skills(doc: dict) -> list[tuple[str, bytes]]:
    source = ROOT / doc["shared"]["skills"]
    out = []
    for skill in sorted(p for p in source.iterdir() if p.is_dir()):
        out.append((skill.name, (skill / "SKILL.md").read_bytes()))
    if not out:
        raise DistributionError("no shared Skills found")
    return out


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _copy_shared(doc: dict, plugin: Path, *, docs: bool) -> None:
    for name, body in _skills(doc):
        dest = plugin / "skills" / name / "SKILL.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
    assets = ROOT / doc["shared"]["assets"]
    for asset in sorted(assets.iterdir()):
        dest = plugin / "assets" / asset.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(asset.read_bytes())
    if docs:
        (plugin / "README.md").write_bytes((ROOT / doc["shared"]["readme"]).read_bytes())
        (plugin / "PRIVACY.md").write_bytes((ROOT / doc["shared"]["privacy"]).read_bytes())


def _interface(doc: dict) -> dict:
    shared = doc["shared_manifest"]["extensions"]["com.openai"]["interface"]
    return {**shared, "displayName": doc["identity"]["display_name"]}


# -- the three adapters ---------------------------------------------------------

def generate_claude_code(doc: dict, out: Path) -> None:
    """The Claude Code adapter: the existing release adapter, under the Fleet identity."""
    fp = _load("fleet_distribution_fleet_plugin", ROOT / "scripts/fleet_plugin.py")
    if (fp.SIDE_PLUGIN_NAME, fp.SIDE_MARKETPLACE_NAME, fp.SIDE_SERVER_NAME) != (
            doc["identity"]["plugin"], doc["identity"]["marketplace"], doc["identity"]["mcp_server"]):
        raise DistributionError("scripts/fleet_plugin.py side identity differs from channels.yaml")
    fp.generate_side(out, doc["server"]["mcp_url"])


def generate_codex(doc: dict, out: Path) -> None:
    """The Codex adapter: `.codex-plugin/plugin.json` + `.codex-mcp.json` + a local marketplace."""
    ident = doc["identity"]
    shared = doc["shared_manifest"]
    plugin = out / "plugins" / ident["plugin"]
    _write_json(plugin / ".codex-plugin" / "plugin.json", {
        "name": ident["plugin"],
        "version": doc["version"],
        "description": shared["description"],
        "author": shared.get("author", {"name": "ProfessorZhi"}),
        "license": shared.get("license", "MIT"),
        "homepage": shared.get("homepage"),
        "repository": shared.get("repository"),
        "keywords": shared.get("keywords", []),
        "skills": "./skills/",
        "mcpServers": "./.codex-mcp.json",
        "interface": _interface(doc),
    })
    # Codex discovers OAuth from the server; no token, client id or secret here.
    _write_json(plugin / ".codex-mcp.json",
                {"mcpServers": {ident["mcp_server"]: {"url": doc["server"]["mcp_url"]}}})
    _copy_shared(doc, plugin, docs=True)
    _write_json(out / ".agents" / "plugins" / "marketplace.json", {
        "name": ident["marketplace"],
        "description": "ServerOS Fleet for Codex, generated from the shared source.",
        "interface": {"displayName": ident["display_name"]},
        "plugins": [{
            "name": ident["plugin"],
            "source": {"source": "local", "path": f"./plugins/{ident['plugin']}"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_USE"},
            "category": "Productivity",
        }],
    })


def generate_openai(doc: dict, out: Path) -> None:
    """ChatGPT (web) and Codex (desktop): skills + icons bound to a REGISTERED App.

    ChatGPT runs a plugin on the web only when its tools come from an App the
    account already registered; a package that declares its own MCP server is
    treated as desktop-only. So this package declares no server at all: it
    names the App (`.app.json`) that holds the Fleet connection and OAuth, and
    carries the shared Skills, icons and the version. The plugin `name` and the
    App id are the ones ChatGPT assigned on first upload (`channels.yaml`), so a
    new upload replaces that plugin instead of adding a second one.
    """
    ident = doc["identity"]
    target = doc["clients"]["openai"]
    name, app_id = target["plugin_name"], target["app_id"]
    shared = doc["shared_manifest"]
    interface = _interface(doc)
    manifest = {
        "$schema": shared["$schema"],
        "name": name,
        "version": doc["version"],
        "description": shared["description"],
        "author": shared.get("author", {"name": "ProfessorZhi"}),
        "license": shared.get("license", "MIT"),
        "homepage": shared.get("homepage"),
        "repository": shared.get("repository"),
        "extensions": {"com.openai": {"apps": "./.app.json", "interface": interface}},
    }
    _write_json(out / "plugin.json", manifest)
    _write_json(out / ".codex-plugin" / "plugin.json", {
        "name": name,
        "version": doc["version"],
        "description": shared["description"],
        "author": shared.get("author", {"name": "ProfessorZhi"}),
        "keywords": shared.get("keywords", []),
        "skills": "./skills",
        "apps": "./.app.json",
        "interface": interface,
    })
    _write_json(out / ".app.json", {"apps": {name: {"id": app_id}}})
    _copy_shared(doc, out, docs=True)
    del ident


def generate_mcp(doc: dict, out: Path) -> None:
    """Any other agent (DeepSeek Harness, custom agents): the server and the Skills.

    No client-specific manifest: a streamable-HTTP MCP entry with OAuth by
    dynamic registration (the agent signs in on the Fleet consent page), the
    same Skills as plain SKILL.md files, the icons and a README saying how to
    wire them. Nothing in it is a credential.
    """
    ident = doc["identity"]
    _write_json(out / "mcp.json", {"mcpServers": {ident["mcp_server"]: {
        "type": "streamable-http",
        "url": doc["server"]["mcp_url"],
        "auth": "oauth2.1 (dynamic client registration; sign in on the Fleet consent page)",
    }}})
    _copy_shared(doc, out, docs=False)
    (out / "README.md").write_text(
        f"# {ident['display_name']} {doc['version']} — for any MCP agent\n\n"
        f"1. Add the server in `mcp.json` to your agent's MCP configuration: "
        f"streamable HTTP at `{doc['server']['mcp_url']}`.\n"
        f"2. On first use the agent opens the Fleet consent page; sign in with your account, "
        f"password and one-time code, and tick only the permissions it needs.\n"
        f"3. Give the agent the Skills in `skills/` (each is one `SKILL.md`), in whatever form "
        f"it loads instructions from.\n\n"
        f"Health: {doc['server'].get('health_url', '')}\n", encoding="utf-8")


GENERATORS = {"claude-code": generate_claude_code, "codex": generate_codex,
              "openai": generate_openai, "mcp": generate_mcp}


# -- build / check ---------------------------------------------------------------

def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _fresh(doc: dict, client: str, tmp: Path) -> Path:
    out = tmp / client
    GENERATORS[client](doc, out)
    return out


def _selected(client: str) -> tuple[str, ...]:
    return CLIENTS if client == "all" else (client,)


def build(client: str = "all") -> int:
    doc = channels()
    for name in _selected(client):
        target = ROOT / doc["clients"][name]["tree"]
        with tempfile.TemporaryDirectory() as tmp:
            fresh = _fresh(doc, name, Path(tmp))
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(fresh, target)
        print(f"wrote {target.relative_to(ROOT)} ({doc['version']})")
    return 0


def stale(client: str = "all") -> list[str]:
    doc = channels()
    found = []
    for name in _selected(client):
        target = ROOT / doc["clients"][name]["tree"]
        with tempfile.TemporaryDirectory() as tmp:
            fresh = _files(_fresh(doc, name, Path(tmp)))
        committed = _files(target) if target.is_dir() else {}
        for rel in sorted(set(fresh) | set(committed)):
            if fresh.get(rel) != committed.get(rel):
                found.append(f"{doc['clients'][name]['tree']}/{rel}")
    return found


def check(client: str = "all") -> int:
    found = stale(client)
    if found:
        print("distribution is stale; run `python3 distribution/build.py build`:", file=sys.stderr)
        for rel in found:
            print(f"  {rel}", file=sys.stderr)
        return 1
    print("every client tree matches the shared source")
    return 0


# -- packaging ---------------------------------------------------------------------

def zip_client(client: str, out_dir: Path) -> Path:
    """A reproducible archive of one committed client tree (for upload)."""
    doc = channels()
    if stale(client):
        raise DistributionError(f"{client} tree is stale; build first")
    tree = ROOT / doc["clients"][client]["tree"]
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{doc['identity']['plugin']}-{client}-v{doc['version']}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, body in _files(tree).items():
            info = zipfile.ZipInfo(rel, date_time=ZIP_TIME)
            info.external_attr = 0o644 << 16
            zf.writestr(info, body, compress_type=zipfile.ZIP_DEFLATED)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    print(f"{archive} sha256={digest}")
    return archive


def install(client: str) -> int:
    doc = channels()
    if client != "claude-code":
        raise DistributionError(
            f"`install` runs only for claude-code; see distribution/README.md for {client}")
    # The resolved path, not the bare name: on Windows the npm-installed CLI is
    # `claude.cmd`, which a bare-name subprocess launch does not find.
    claude = shutil.which("claude")
    if claude is None:
        print("the `claude` command is not on PATH", file=sys.stderr)
        return 2
    if stale(client):
        print("the claude-code tree is stale; run build first", file=sys.stderr)
        return 1
    tree = ROOT / doc["clients"][client]["tree"]
    plugin_id = doc["clients"][client]["install_id"]

    def run(*args: str, ok: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run([claude, "plugin", *args], text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if ok and proc.returncode != 0:
            raise DistributionError(f"`claude plugin {' '.join(args)}` failed:\n{proc.stdout}")
        return proc

    # The identities this one replaces go first, so an install is in place:
    # one Fleet plugin before, one after.
    for old in doc["identity"].get("replaces") or []:
        run("uninstall", old["install_id"], ok=False)
        run("marketplace", "remove", old["marketplace"], ok=False)
    run("uninstall", plugin_id, ok=False)
    run("marketplace", "remove", doc["identity"]["marketplace"], ok=False)
    run("marketplace", "add", str(tree))
    run("install", plugin_id)
    print(f"installed {plugin_id} {doc['version']} from {tree.relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("build", "check", "zip", "install"))
    parser.add_argument("--client", default="all", choices=("all",) + CLIENTS)
    parser.add_argument("--out", default="dist")
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            return build(args.client)
        if args.command == "check":
            return check(args.client)
        if args.command == "zip":
            if args.client == "all":
                raise DistributionError("zip one client at a time")
            zip_client(args.client, Path(args.out))
            return 0
        return install(args.client)
    except DistributionError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
