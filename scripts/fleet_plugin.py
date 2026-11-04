#!/usr/bin/env python3
"""Build, check and install the ServerOS Fleet Claude Code plugin from this repo.

The repository root is a Claude Code marketplace (`.claude-plugin/marketplace.json`)
whose one plugin lives, already generated, in `plugins/serveros-fleet-ingress/`.
That tree is produced by `integrations/fleet-cc-dev-plugin/build_release_adapter.py`
from the shared Skill source in `integrations/fleet-v1-first-install/plugin/`, and
it is committed so that a clone — or `claude plugin marketplace add
ProfessorZhi/serveros` — can install it without running anything.

    python3 scripts/fleet_plugin.py build     # regenerate the committed tree
    python3 scripts/fleet_plugin.py check     # fail if the tree is stale
    python3 scripts/fleet_plugin.py install   # one-click install into Claude Code

Edit Skills in the shared source, run `build`, commit both. `check` (and the
test that calls it) refuses a commit where the two disagree.

`install` registers this checkout as the `serveros` marketplace, removes any
earlier copy of the plugin (including the old `@fleet-local` one, unless
`--keep-legacy`), and installs `serveros-fleet-ingress@serveros`. The first
Fleet tool call then opens the server's sign-in page.

    python3 scripts/fleet_plugin.py install-side --next-mcp-url URL

`install-side` installs the same plugin a SECOND time, side by side, without
touching the one in service: plugin `serveros-fleet-v1@serveros-v1`, MCP server
`serveros-fleet-v1`, generated into `~/.cache/serveros/fleet-plugin-side/`. Point
it at the V1 candidate surface (a second Fleet instance on its own port). Both
plugins load together; their tools carry different server names. Remove it with
`claude plugin uninstall serveros-fleet-v1@serveros-v1`.
"""
from __future__ import annotations

import argparse
import filecmp
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADAPTER = ROOT / "integrations" / "fleet-cc-dev-plugin" / "build_release_adapter.py"
PLUGIN_NAME = "serveros-fleet-ingress"
MARKETPLACE_NAME = "serveros"
PLUGIN_DIR = ROOT / "plugins" / PLUGIN_NAME
MARKETPLACE_FILE = ROOT / ".claude-plugin" / "marketplace.json"
LEGACY_IDS = (f"{PLUGIN_NAME}@fleet-local",)
#: The side-by-side channel: a second, separately named copy of the plugin.
SIDE_PLUGIN_NAME = "serveros-fleet-v1"
SIDE_MARKETPLACE_NAME = "serveros-v1"
SIDE_SERVER_NAME = "serveros-fleet-v1"
SIDE_DISPLAY_NAME = "ServerOS Fleet V1"
SIDE_ROOT = Path.home() / ".cache" / "serveros" / "fleet-plugin-side"

#: The Fleet Next ingress the committed plugin points at. Override with
#: `--next-mcp-url` or `SERVEROS_FLEET_MCP_URL` when building for another one.
DEFAULT_NEXT_MCP_URL = "https://serveros-fleet-next.tail3d8b76.ts.net/mcp"


def _adapter():
    spec = importlib.util.spec_from_file_location("fleet_plugin_release_adapter", ADAPTER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def marketplace(record: dict) -> dict:
    return {
        "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
        "name": MARKETPLACE_NAME,
        "description": "ServerOS plugins, generated from this repository.",
        "owner": {"name": "ProfessorZhi"},
        "plugins": [{
            "name": record["name"],
            "source": f"./plugins/{PLUGIN_NAME}",
            "description": ("Reach your ServerOS devices and their Claude Code / "
                            "Codex sessions through the Fleet MCP server, with "
                            "the operator Skills that teach how."),
            "version": record["version"],
            "category": "productivity",
        }],
    }


def generate(out_root: Path, url: str) -> tuple[Path, Path]:
    """Write the plugin tree and marketplace file under `out_root`."""
    plugin = out_root / "plugins" / PLUGIN_NAME
    plugin.parent.mkdir(parents=True)
    record = _adapter().generate(out=plugin, next_mcp_url=url)
    market = out_root / ".claude-plugin" / "marketplace.json"
    market.parent.mkdir(parents=True)
    market.write_text(json.dumps(marketplace(record), indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    return plugin, market


def _tree(path: Path) -> list[str]:
    return sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())


def differences(url: str) -> list[str]:
    """What differs between the committed tree and a fresh generation."""
    with tempfile.TemporaryDirectory() as tmp:
        plugin, market = generate(Path(tmp), url)
        found = []
        if not MARKETPLACE_FILE.is_file() or not filecmp.cmp(market, MARKETPLACE_FILE, shallow=False):
            found.append(str(MARKETPLACE_FILE.relative_to(ROOT)))
        fresh, committed = _tree(plugin), _tree(PLUGIN_DIR) if PLUGIN_DIR.is_dir() else []
        for rel in sorted(set(fresh) | set(committed)):
            a, b = plugin / rel, PLUGIN_DIR / rel
            if not a.is_file() or not b.is_file() or not filecmp.cmp(a, b, shallow=False):
                found.append(f"plugins/{PLUGIN_NAME}/{rel}")
        return found


def build(url: str) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        plugin, market = generate(Path(tmp), url)
        if PLUGIN_DIR.exists():
            shutil.rmtree(PLUGIN_DIR)
        shutil.copytree(plugin, PLUGIN_DIR)
        MARKETPLACE_FILE.parent.mkdir(exist_ok=True)
        shutil.copyfile(market, MARKETPLACE_FILE)
    print(f"wrote {PLUGIN_DIR.relative_to(ROOT)} and {MARKETPLACE_FILE.relative_to(ROOT)}")
    return 0


def check(url: str) -> int:
    stale = differences(url)
    if stale:
        print("the committed plugin is stale; run `python3 scripts/fleet_plugin.py build`:",
              file=sys.stderr)
        for rel in stale:
            print(f"  {rel}", file=sys.stderr)
        return 1
    print("committed plugin matches its source")
    return 0


def _claude(*args: str, check_ok: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["claude", "plugin", *args], text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if check_ok and proc.returncode != 0:
        print(proc.stdout, file=sys.stderr)
        raise SystemExit(f"`claude plugin {' '.join(args)}` failed ({proc.returncode})")
    return proc


def install(url: str, keep_legacy: bool) -> int:
    if shutil.which("claude") is None:
        print("the `claude` command is not on PATH; install Claude Code first", file=sys.stderr)
        return 2
    if check(url) != 0:
        return 1
    # Uninstall first: a plugin is addressed through its marketplace, so the
    # old copy has to go while that marketplace is still registered.
    targets = [f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"]
    if not keep_legacy:
        targets += LEGACY_IDS
    for plugin_id in targets:
        removed = _claude("uninstall", plugin_id, check_ok=False)
        if removed.returncode == 0:
            print(f"removed {plugin_id}")
    known = _claude("marketplace", "list", "--json")
    try:
        names = {m.get("name") for m in json.loads(known.stdout or "[]")}
    except json.JSONDecodeError:
        names = set()
    if MARKETPLACE_NAME in names:
        _claude("marketplace", "remove", MARKETPLACE_NAME)
    _claude("marketplace", "add", str(ROOT))
    _claude("install", f"{PLUGIN_NAME}@{MARKETPLACE_NAME}")
    print(f"installed {PLUGIN_NAME}@{MARKETPLACE_NAME} from {ROOT}")
    print("restart Claude Code; the first Fleet tool call opens the sign-in page")
    return 0


def generate_side(out_root: Path, url: str) -> Path:
    """The plugin under a second name, for running beside the one in service.

    Same skills and assets; only the identities change — plugin name, display
    name, MCP server key and marketplace — so neither copy shadows the other.
    """
    with tempfile.TemporaryDirectory() as tmp:
        plugin, _ = generate(Path(tmp), url)
        if out_root.exists():
            shutil.rmtree(out_root)
        target = out_root / "plugins" / SIDE_PLUGIN_NAME
        shutil.copytree(plugin, target)
    manifest_path = target / ".claude-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["name"] = SIDE_PLUGIN_NAME
    manifest["displayName"] = SIDE_DISPLAY_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    mcp_path = target / ".mcp.json"
    servers = json.loads(mcp_path.read_text(encoding="utf-8"))["mcpServers"]
    (only,) = servers.values()
    if isinstance(only.get("oauth"), dict) and isinstance(only["oauth"].get("callbackPort"), int):
        # Its own OAuth callback port, so signing in to one never lands on the other.
        only["oauth"]["callbackPort"] += 1
    mcp_path.write_text(json.dumps({"mcpServers": {SIDE_SERVER_NAME: only}}, indent=2,
                                   ensure_ascii=False) + "\n", encoding="utf-8")
    market = marketplace(manifest)
    market["name"] = SIDE_MARKETPLACE_NAME
    market["plugins"][0]["source"] = f"./plugins/{SIDE_PLUGIN_NAME}"
    market["description"] = "ServerOS Fleet V1, installed beside any other Fleet plugin."
    market_path = out_root / ".claude-plugin" / "marketplace.json"
    market_path.parent.mkdir(parents=True, exist_ok=True)
    market_path.write_text(json.dumps(market, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    return target


def install_side(url: str, out_root: Path = SIDE_ROOT) -> int:
    if shutil.which("claude") is None:
        print("the `claude` command is not on PATH; install Claude Code first", file=sys.stderr)
        return 2
    generate_side(out_root, url)
    plugin_id = f"{SIDE_PLUGIN_NAME}@{SIDE_MARKETPLACE_NAME}"
    _claude("uninstall", plugin_id, check_ok=False)
    known = _claude("marketplace", "list", "--json")
    try:
        names = {m.get("name") for m in json.loads(known.stdout or "[]")}
    except json.JSONDecodeError:
        names = set()
    if SIDE_MARKETPLACE_NAME in names:
        _claude("marketplace", "remove", SIDE_MARKETPLACE_NAME)
    _claude("marketplace", "add", str(out_root))
    _claude("install", plugin_id)
    print(f"installed {plugin_id} beside {PLUGIN_NAME}@{MARKETPLACE_NAME} (untouched), "
          f"pointing at {url}")
    print("restart Claude Code; tools appear under the server name "
          f"`{SIDE_SERVER_NAME}`")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("build", "check", "install", "install-side"))
    parser.add_argument("--next-mcp-url",
                        default=os.environ.get("SERVEROS_FLEET_MCP_URL", DEFAULT_NEXT_MCP_URL))
    parser.add_argument("--keep-legacy", action="store_true",
                        help=f"do not uninstall {', '.join(LEGACY_IDS)}")
    args = parser.parse_args(argv)
    if args.command == "build":
        return build(args.next_mcp_url)
    if args.command == "check":
        return check(args.next_mcp_url)
    if args.command == "install-side":
        return install_side(args.next_mcp_url)
    return install(args.next_mcp_url, args.keep_legacy)


if __name__ == "__main__":
    raise SystemExit(main())
