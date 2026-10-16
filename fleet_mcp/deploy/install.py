#!/usr/bin/env python3
"""Place this surface on a POSIX host as a user service — and take it back off.

The shape is the one this host already uses for the node runtime
----------------------------------------------------------------
    <runtime-root>/<composition>/revisions/<label>@<shortrev>/   one revision, immutable
    <runtime-root>/<composition>/current                         a symlink to one of them
    <runtime-root>/<composition>/previous                        the one before it

so `current` is what the unit runs, and a rollback is one symlink and one
restart. Nothing in this file is a new idea about deployment; it is the layout
`~/runtime-assets/hwz-server-os/` was already using, applied to a second
surface.

Why not `scripts/release.py`
----------------------------
Because it is not generic, whatever its flags suggest: it carries
`UNIT_NAME = "serverosd.service"`, and pointing that unit at this artifact would
stop the node runtime this host is running. A second caller of a single-caller
installer is how a node gets replaced by a web surface, so this is a separate
installer with the same guarantees rather than a flag added to that one.

What this does NOT do
---------------------
It does not write a binding, mint a credential, or create an anchor. A service
started without a binding refuses at startup with exit 3 and says which file it
looked for — which is the behaviour this installer relies on rather than works
around. `--config` names the binding; if it is not there, this refuses.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - reported, not guessed at
    sys.stderr.write(
        "install.py: PyYAML is required to read the binding and to render the "
        "unit; the system python3 on this host has it (fleet/transport.py "
        "refuses for the same reason).\n"
    )
    raise SystemExit(2)

COMPOSITION = "ingress"
UNIT_NAME = "serveros-fleet-mcp.service"
SERVICE_NAME = "serveros-fleet-mcp"
ARTIFACT_SCHEMA = "serveros/fleet-mcp-artifact/v1"
ENTRY = "fleet_mcp/bin/serveros-fleet-mcp"
# The two directories the running surface reads: its own package, and the
# identity layer it refuses to keep a second copy of. `fleet/` travels with the
# artifact because the identity code reads its contracts from beside itself;
# WHICH devices exist is still read from the registry the binding names.
PAYLOAD = ("fleet_mcp", "fleet")
RUNTIME_PLACEHOLDER = "@FLEET_MCP_RUNTIME@"
CONFIG_PLACEHOLDER = "@FLEET_MCP_CONFIG@"
UNIT_TEMPLATE = "fleet_mcp/deploy/serveros-fleet-mcp.service"
DEFAULT_CONFIG = "~/.config/serveros-fleet-mcp/fleet-mcp.local.yaml"
MANIFEST = "ARTIFACT.json"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")


def die(message: str, code: int = 2) -> None:
    sys.stderr.write(f"install.py: {message}\n")
    raise SystemExit(code)


def systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    command = ["systemctl", "--user", *args]
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        die(f"{' '.join(command)} failed ({result.returncode}): {detail}\n"
            f"  this installer installs a USER unit through `systemctl --user`; "
            f"a host without a user manager has no unit to install.")
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def git_revision(source: Path) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def tree_state(source: Path) -> str | None:
    """Why a git source cannot be published as-is, or None when it is clean.

    A revision name is a claim about BYTES, and this installer copies `fleet_mcp/`
    and `fleet/` from the FILESYSTEM, not from the object store. So matching
    `--revision` to `HEAD` is not enough: if the working tree carries uncommitted
    edits, the copied bytes are not the commit, and `ARTIFACT.json` would name a
    revision those bytes do not match. This is the check that makes the claim
    true rather than merely well-typed.

    The REAL git dir is resolved with `git rev-parse --absolute-git-dir` instead
    of assuming `<source>/.git`: in a linked worktree `.git` is a FILE holding a
    `gitdir:` pointer, so the index lock lives wherever that pointer leads, and a
    path guess would look for a lock that cannot exist there.

    Ignored files are deliberately NOT counted. `git status` excludes them, and
    `.gitignore` is exactly where build residue belongs — requiring a tree with
    no ignored files would refuse every normal checkout. Tracked edits and
    untracked non-ignored paths are the dirty tree; `--untracked-files=all`
    lists them individually rather than collapsing a directory to one entry.

    Every unreadable answer is a refusal: this fails closed.
    """
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(source), *args],
                              capture_output=True, text=True)
    try:
        gitdir = git("rev-parse", "--absolute-git-dir")
    except OSError as exc:
        return (f"cannot read the git directory of {source} "
                f"({type(exc).__name__}); refusing to publish blind")
    if gitdir.returncode != 0 or not gitdir.stdout.strip():
        # `git_revision` already read a HEAD from here, so a gitdir that cannot
        # be resolved now is a repository that changed under us, not a non-git
        # source. Refuse rather than fall through to publishing.
        return (f"cannot read the git directory of {source}; refusing to "
                f"publish an unverifiable tree")
    lock = Path(gitdir.stdout.strip()) / "index.lock"
    if lock.exists():
        return (f"another git process holds {lock}; the tree can change during "
                f"the copy, so the artifact would not be the revision it names")
    try:
        status = git("status", "--porcelain", "--untracked-files=all")
    except OSError as exc:
        return (f"cannot read the working-tree state of {source} "
                f"({type(exc).__name__}); refusing to publish blind")
    if status.returncode != 0:
        return (f"cannot read the working-tree state of {source} "
                f"(git status exited {status.returncode})")
    if status.stdout.strip():
        count = len(status.stdout.splitlines())
        return (f"{source} has {count} uncommitted path(s) (tracked edits or "
                f"untracked files); the installer copies the working tree, so "
                f"the artifact would carry a revision name those bytes do not "
                f"match. Commit them, or point --source at a clean tree.")
    return None


def read_link(path: Path) -> Path | None:
    if not path.is_symlink():
        return None
    try:
        return path.resolve()
    except OSError:
        return None


def point(link: Path, target: Path | None) -> None:
    """Swap a symlink atomically, or remove it.

    A symlink is created beside the destination and renamed over it, because
    `os.replace` onto a symlink replaces the link itself rather than writing
    through it. Removing the old link first would leave a window in which
    `current` does not exist, and a restarted unit reading it in that window
    would report a missing installation rather than an old one.
    """
    if target is None:
        if link.is_symlink():
            link.unlink()
        return
    swap = link.with_name(link.name + ".swap")
    if swap.is_symlink() or swap.exists():
        swap.unlink()
    swap.symlink_to(target)
    os.replace(swap, link)


def manifest_of(root: Path) -> dict:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            files[str(path.relative_to(root))] = sha256_file(path)
    tree = hashlib.sha256(
        "\n".join(f"{name} {digest}" for name, digest in sorted(files.items())).encode()
    ).hexdigest()
    return {"files": files, "tree_sha256": tree}


def binding(config_path: Path) -> dict:
    if not config_path.exists():
        die(f"no binding at {config_path}. This installer does not create one: a "
            f"service started without a binding refuses at startup, and a binding "
            f"it invented would be a claim about devices nothing verified.")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    server = data.get("server") or {}
    bind = str(server.get("bind") or "127.0.0.1")
    port = server.get("port")
    if not isinstance(port, int):
        die(f"the binding at {config_path} declares no server.port; the unit's "
            f"health check would have nothing to probe.")
    probe_host = "127.0.0.1" if bind in ("0.0.0.0", "::") else bind
    return {"bind": bind, "port": port,
            "mcp_path": str(server.get("mcp_path") or "/mcp"),
            "probe": f"http://{probe_host}:{port}",
            "device_ref": data.get("device_ref")}


def render_unit(template: str, runtime: Path, config_path: Path) -> str:
    for placeholder in (RUNTIME_PLACEHOLDER, CONFIG_PLACEHOLDER):
        if placeholder not in template:
            die(f"the unit template at {UNIT_TEMPLATE} does not name {placeholder}; "
                f"rendering it would produce a unit whose ExecStart points at a "
                f"literal placeholder.")
    return (template.replace(RUNTIME_PLACEHOLDER, str(runtime))
                    .replace(CONFIG_PLACEHOLDER, str(config_path)))


def wait_for_health(base: str, seconds: float = 20.0) -> dict | None:
    deadline = time.monotonic() + seconds
    last: str | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=2) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = str(exc)
            time.sleep(0.5)
    sys.stderr.write(f"install.py: no health answer from {base}/health within "
                     f"{seconds:.0f}s (last: {last})\n")
    return None


def install(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser().resolve()
    head = git_revision(source)
    # `--revision` is the route for a source that is NOT a git repository: there
    # is no HEAD to read, so the caller must name the bytes. On a git source it
    # is not an override. The revision names the directory and is written into
    # ARTIFACT.json, so a caller-chosen SHA would tie those bytes to a commit
    # that never described them — and, for a starting install, would be the
    # identity the running service reports. A mismatch is refused rather than
    # ignored: silently publishing HEAD under the caller's name would be a
    # different lie, about which revision was asked for.
    if args.revision and head and args.revision != head:
        die(f"--revision {args.revision!r} is not the git HEAD of {source} "
            f"({head!r}). A revision name records which bytes the artifact was "
            f"built from; drop --revision to use the HEAD, or point --source at "
            f"the tree those bytes are in. --revision stays available for a "
            f"source that is not a git repository at all.")
    revision = args.revision or head
    if not revision:
        die(f"cannot read a git revision from {source}; pass --revision so the "
            f"artifact records which bytes it was built from.")
    # The revision must not only be NAMED right, the tree must BE it. This
    # installer copies `PAYLOAD` from the filesystem, so a dirty working tree
    # would publish bytes under a revision name that never described them —
    # including in the `--revision == HEAD` and `--revision`-omitted cases that
    # the check above cannot distinguish. Refuse here: before the binding is
    # read, before anything is copied, and before any systemctl action. A source
    # that is not a git repository makes no HEAD claim, so this does not apply
    # to it and `--revision` stays its route.
    if head:
        problem = tree_state(source)
        if problem:
            die(problem)
    config_path = Path(args.config).expanduser()
    info = binding(config_path)
    label = args.label or f"fleet-mcp-{dt.date.today():%Y%m%d}"
    composition_root = Path(args.runtime_root).expanduser() / args.composition
    revision_dir = composition_root / "revisions" / f"{label}@{revision[:7]}"
    staging = composition_root / f".staging-{label}-{dt.datetime.now():%Y%m%d%H%M%S}"

    if revision_dir.exists() and not args.force:
        die(f"{revision_dir} already exists. A revision directory is immutable "
            f"once installed; pass --force to replace it, or --label to publish "
            f"these bytes under a new name.")
    for name in PAYLOAD:
        if not (source / name).is_dir():
            die(f"{source / name} is not a directory; {name}/ is part of the payload")

    plan = {
        "unit": UNIT_NAME,
        "composition": args.composition,
        "runtime_root": str(Path(args.runtime_root).expanduser()),
        "revision": revision,
        "label": label,
        "revision_dir": str(revision_dir),
        "config": str(config_path),
        "device_ref": info["device_ref"],
        "mcp_endpoint": f"{info['probe']}{info['mcp_path']}",
        "health": f"{info['probe']}/health",
    }
    if args.dry_run:
        print(json.dumps({"dry_run": "install", **plan}, indent=2))
        return 0

    composition_root.mkdir(parents=True, exist_ok=True)
    if staging.exists():
        shutil.rmtree(staging)
    for name in PAYLOAD:
        shutil.copytree(source / name, staging / name, ignore=IGNORE)
    entry = staging / ENTRY
    if not entry.is_file():
        die(f"the payload has no {ENTRY}; the repository declares one, so this is "
            f"a source layout problem rather than a host problem")
    if not os.access(entry, os.X_OK):
        die(f"{ENTRY} is not executable in the artifact. Every launcher in this "
            f"repository is committed 100755; a copy that lost the bit is a "
            f"revision that cannot start, and `git ls-files -s` says which "
            f"commit introduced it.")
    template = (staging / UNIT_TEMPLATE).read_text(encoding="utf-8")
    manifest = {
        "schema": ARTIFACT_SCHEMA,
        "name": SERVICE_NAME,
        "composition": args.composition,
        "label": label,
        "revision": revision,
        "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "built_from": str(source),
        "entry": ENTRY,
        "unit": UNIT_NAME,
        "rendered_for": {"config": str(config_path), "device_ref": info["device_ref"]},
        **manifest_of(staging),
    }
    (staging / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    revision_dir.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(revision_dir)

    previous = read_link(composition_root / "current")
    if previous is not None:
        point(composition_root / "previous", previous)
    point(composition_root / "current", revision_dir)

    runtime = composition_root / "current"
    unit_path = Path(args.unit_dir).expanduser() / UNIT_NAME
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    swap = unit_path.with_name(unit_path.name + ".swap")
    swap.write_text(render_unit(template, runtime, config_path), encoding="utf-8")
    # Never inherit a permissive process umask for an executable user unit:
    # the rendered unit points at this user\u0027s private runtime/config paths.
    swap.chmod(0o600)
    os.replace(swap, unit_path)
    systemctl("daemon-reload")

    if args.no_start:
        print(json.dumps({**plan, "tree_sha256": manifest["tree_sha256"],
                          "unit_path": str(unit_path), "started": False,
                          "next": f"systemctl --user start {UNIT_NAME}"},
                         indent=2))
        return 0

    # `enable --now` is a START, and a start on a unit that is ALREADY running is
    # a no-op: the first install needs it, every later one is a lie. Installing a
    # revision over a live service therefore left the PREVIOUS process serving,
    # and this function still reported `started: true` — because `health` was
    # answered by that same old process. A deploy that silently keeps running the
    # code it just replaced is precisely the failure this installer exists to
    # make visible, so an install over an active unit is restarted.
    live = systemctl("is-active", UNIT_NAME, check=False).stdout.strip() == "active"
    if live:
        systemctl("enable", UNIT_NAME)
        systemctl("restart", UNIT_NAME)
    else:
        systemctl("enable", "--now", UNIT_NAME)
    health = wait_for_health(info["probe"], args.health_timeout)
    started = health is not None
    if not started:
        systemctl("status", UNIT_NAME, "--no-pager", check=False)
    print(json.dumps({**plan, "tree_sha256": manifest["tree_sha256"],
                      "files": len(manifest["files"]),
                      "unit_path": str(unit_path), "started": started, "health": health},
                     indent=2))
    if not started:
        die(f"{UNIT_NAME} is installed and did not answer on {plan['health']}; "
            f"`journalctl --user -u {UNIT_NAME} -n 40` has the reason", code=3)
    return 0


def rollback(args: argparse.Namespace) -> int:
    composition_root = Path(args.runtime_root).expanduser() / args.composition
    current = read_link(composition_root / "current")
    previous = read_link(composition_root / "previous")
    if current is None:
        die(f"no `current` under {composition_root}; there is nothing to roll back from")
    if previous is None or not previous.exists():
        die(f"no usable `previous` under {composition_root}; this installation has "
            f"only ever had one revision, so a rollback would be a no-op wearing "
            f"the word 'rollback'")
    if args.dry_run:
        print(json.dumps({"dry_run": "rollback", "from": str(current), "to": str(previous)},
                         indent=2))
        return 0
    point(composition_root / "previous", current)
    point(composition_root / "current", previous)
    systemctl("daemon-reload")
    systemctl("restart", UNIT_NAME)
    info = binding(Path(args.config).expanduser())
    health = wait_for_health(info["probe"], args.health_timeout)
    print(json.dumps({"rolled_back_from": str(current), "now": str(previous),
                      "unit": UNIT_NAME, "health": health}, indent=2))
    return 0 if health else 3


def status(args: argparse.Namespace) -> int:
    composition_root = Path(args.runtime_root).expanduser() / args.composition
    current = read_link(composition_root / "current")
    unit_path = Path(args.unit_dir).expanduser() / UNIT_NAME
    state = systemctl("show", UNIT_NAME, "-p", "ActiveState", "-p", "SubState",
                      "-p", "MainPID", "-p", "ExecMainStartTimestamp", check=False)
    report = {
        "unit": UNIT_NAME,
        "unit_file": str(unit_path),
        "unit_file_present": unit_path.exists(),
        "current": str(current) if current else None,
        "previous": str(read_link(composition_root / "previous") or "") or None,
        "systemd": dict(
            line.split("=", 1) for line in state.stdout.strip().splitlines() if "=" in line
        ),
    }
    if current and (current / MANIFEST).exists():
        manifest = json.loads((current / MANIFEST).read_text(encoding="utf-8"))
        report["artifact"] = {key: manifest.get(key) for key in
                              ("label", "revision", "created", "tree_sha256", "unit")}
        report["artifact"]["files"] = len(manifest.get("files") or {})
    if args.config:
        config_path = Path(args.config).expanduser()
        if config_path.exists():
            info = binding(config_path)
            report["endpoint"] = f"{info['probe']}{info['mcp_path']}"
            report["health_url"] = f"{info['probe']}/health"
            report["health"] = wait_for_health(info["probe"], 3.0)
    print(json.dumps(report, indent=2))
    return 0


def uninstall(args: argparse.Namespace) -> int:
    composition_root = Path(args.runtime_root).expanduser() / args.composition
    unit_path = Path(args.unit_dir).expanduser() / UNIT_NAME
    if args.dry_run:
        print(json.dumps({"dry_run": "uninstall", "unit": UNIT_NAME,
                          "unit_file": str(unit_path),
                          "removes_revisions": bool(args.purge)}, indent=2))
        return 0
    systemctl("disable", "--now", UNIT_NAME, check=False)
    if unit_path.exists():
        unit_path.unlink()
        print(f"removed {unit_path}")
    else:
        print(f"no unit file at {unit_path} (already removed, or installed elsewhere)")
    systemctl("daemon-reload", check=False)
    if args.purge:
        if composition_root.exists():
            shutil.rmtree(composition_root)
            print(f"removed {composition_root} and every revision in it")
    else:
        print(f"kept {composition_root} (revisions stay installed)")
    print("The binding, the owner token and the audit log were not touched: they "
          "are this host's files, not this service's.")
    return 0


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--runtime-root", default="~/runtime-assets/serveros-fleet-mcp",
                        help="distribution root (default: %(default)s)")
    common.add_argument("--composition", default=COMPOSITION)
    common.add_argument("--unit-dir", default="~/.config/systemd/user")
    common.add_argument("--config", default=DEFAULT_CONFIG)
    common.add_argument("--health-timeout", type=float, default=20.0)
    common.add_argument("--dry-run", action="store_true")

    top = argparse.ArgumentParser(
        prog="install.py", description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = top.add_subparsers(dest="command", required=True)

    add = sub.add_parser("install", parents=[common], help="place a revision and start it")
    add.add_argument("--source", default=str(Path(__file__).resolve().parents[2]))
    add.add_argument("--revision", default=None, help="default: git HEAD of --source")
    add.add_argument("--label", default=None, help="default: fleet-mcp-<YYYYMMDD>")
    add.add_argument("--force", action="store_true")
    add.add_argument("--no-start", action="store_true")
    add.set_defaults(func=install)

    back = sub.add_parser("rollback", parents=[common], help="point `current` back one revision")
    back.set_defaults(func=rollback)

    show = sub.add_parser("status", parents=[common], help="what is installed and what answers")
    show.set_defaults(func=status)

    off = sub.add_parser("uninstall", parents=[common], help="stop, disable, remove the unit")
    off.add_argument("--purge", action="store_true", help="also delete every installed revision")
    off.set_defaults(func=uninstall)
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
