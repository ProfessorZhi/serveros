"""Delegate Context Capsule resolution to the ServerOS bundle.

The Capsule resolver is a ServerOS product component. It is canonical in
`ProfessorZhi/serveros` and ships inside the generated runtime artifact, so this
module only locates the bundle and invokes the resolver. The CLI therefore
depends on a stable distribution path and never on a movable source checkout.

The resolver reads Item cards and project policy as YAML. `workengine` itself
stays dependency-free, so this module does not import PyYAML: it runs the
resolver as a subprocess under an interpreter that can satisfy it.

Roots are resolved here, once, and then passed to the resolver explicitly. The
two processes must never guess from the environment independently, or the CLI
and the Capsule could read two different worlds.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# `_absolute_override` is imported rather than `ServerOS`, because constructing
# `ServerOS` creates and migrates the state database. A Capsule resolve is
# read-only, so it must not touch state just to learn where state lives.
from .engine import (
    default_artifacts_dir,
    default_runtime_assets_root,
    default_state_dir,
    default_workspace_root,
)

BUNDLE_RELATIVE = "runtime-assets/hwz-server-os/current"
# The same location, addressed from the runtime-assets role instead of from a
# data root. The device binds `runtime_assets_root` directly; deriving it from
# a data root only worked on machines whose assets happened to sit there.
BUNDLE_IN_ASSETS = "hwz-server-os/current"
RESOLVER_RELATIVE = "context/resolver.py"


def workspace_root() -> Path:
    """The workspace root on the device that is running.

    Resolved by the engine, which asks the device's own host profile rather
    than assuming a layout. Named here because the Context Capsule reads it to
    decide which source tree a Capsule refers to.
    """
    return default_workspace_root()


def resolved_roots(args=None) -> dict[str, Path]:
    """The roots this CLI is actually using, resolved exactly as the commands do."""
    state_dir = (
        Path(args.state_dir).resolve()
        if getattr(args, "state_dir", None)
        else default_state_dir()
    )
    artifacts_root = (
        Path(args.artifacts_root).resolve()
        if getattr(args, "artifacts_root", None)
        else default_artifacts_dir()
    )
    return {"state_dir": state_dir, "artifacts_root": artifacts_root, "workspace_root": workspace_root()}


def bundle_candidates() -> list[Path]:
    """Where the runtime artifact may live, most authoritative first."""
    roots: list[Path] = []
    explicit = os.environ.get("SERVEROS_BUNDLE")
    if explicit:
        roots.append(Path(explicit))
    roots.append(default_runtime_assets_root() / BUNDLE_IN_ASSETS)
    roots.append(workspace_root() / "src/serveros")  # development source tree
    return roots


def resolver_path() -> Path:
    candidates = bundle_candidates()
    for root in candidates:
        candidate = root / RESOLVER_RELATIVE
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "ServerOS Context Capsule resolver not found; searched: "
        + ", ".join(str(root / RESOLVER_RELATIVE) for root in candidates)
        + ". Set SERVEROS_BUNDLE to the runtime artifact root."
    )


def _has_yaml(interpreter: str) -> bool:
    try:
        proc = subprocess.run(
            [interpreter, "-c", "import yaml"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _interpreter() -> str:
    """An interpreter able to run the resolver, which requires PyYAML."""
    for candidate in (os.environ.get("SERVEROS_PYTHON"), sys.executable, shutil.which("python3")):
        if candidate and _has_yaml(candidate):
            return candidate
    raise FileNotFoundError(
        "no Python interpreter with PyYAML is available to run the Context Capsule "
        "resolver; set SERVEROS_PYTHON to one that has it"
    )


def resolve_capsule(item: str | None = None, cwd: str | None = None, args=None) -> dict:
    """Resolve an Item's Capsule, or infer the Item from cwd when it is omitted."""
    path = resolver_path()
    roots = resolved_roots(args)
    argv = [
        _interpreter(), str(path),
        "--json",
        "--workspace-root", str(roots["workspace_root"]),
        "--artifacts-root", str(roots["artifacts_root"]),
        "--state-dir", str(roots["state_dir"]),
    ]
    if item:
        argv.append(item)
    if cwd:
        argv += ["--cwd", cwd]
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise ValueError(proc.stderr.strip() or f"Capsule resolve failed ({proc.returncode})")
    return json.loads(proc.stdout)
