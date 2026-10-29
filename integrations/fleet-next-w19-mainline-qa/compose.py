"""W19 mainline QA: a narrow, verifiable candidate built from exact commits.

The W19 question is not "does #507 pass its own old-tree CI?" — it has 270
changed files and 67,976 insertions relative to main, including runtime,
plugin, serverosd, native, plugins, fleet and scripts. It is not a mainline
landing candidate.

This module builds a **read-only candidate fixture** instead:

    main 3bc7adf
      + only the Fleet Next monitor/activation/adapter/notify/batch/observer/
        runner/sources integration directories, byte-for-byte from #507 05da04f
      + the three runtime-tick tests from #507

It never copies `runtime/`, `plugin/`, `serverosd/`, `native/`, `plugins/`,
`fleet/`, `scripts/`, `.github/`, `fleet_mcp/` or any other production path.
The main tree and the RC tree are both fetched by exact SHA; every candidate
file is compared to its #507 blob. A path outside the allowlist is a veto, not
a warning.

No public landing PR exists yet, so this is a fixture, not a signed landing.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

MAIN = "3bc7adfe40274741cc528c7dd96167d2b67b00ec"
RC = "05da04f8c78f302b029c6304ed96f4e8ded5fc9b"
QA508 = "fb8c435637e486fc0ecf78a2ea83b9b323c936bb"
WAVE18 = "80220a4ad1b009946f11bda0ce96dd8ff8e05468"

ALLOWED_DIRS = (
    "integrations/fleet-next-agent-monitor",
    "integrations/fleet-next-agent-monitor-activation",
    "integrations/fleet-next-monitor-source-adapter",
    "integrations/fleet-v1-milestone-notify",
    "integrations/fleet-v1-milestone-batch",
    "integrations/fleet-v1-milestone-observer",
    "integrations/fleet-v1-milestone-runner",
    "integrations/fleet-v1-milestone-sources",
)

RUNTIME_TESTS = (
    "tests/test_fleet_next_agent_monitor_activation_runtime_tick.py",
)

BATCH_DIR = "integrations/fleet-v1-milestone-batch"

#: Production / legacy / packaging paths that may never enter a Fleet Next
#: monitor landing delta. A match is a veto, not a review note.
FORBIDDEN_PREFIXES = (
    "fleet_mcp/", "runtime/", "plugin/", "plugins/", "serverosd/",
    "native/", "fleet/", "scripts/", ".github/", ".claude-plugin/",
    "fleet_hub/", "context/", "abilities/", "mcp/", "skills/",
    "deploy/", "services/", "bin/", "workengine/",
    "tests/hermetic-known-failures.txt", "pytest.ini", "pyproject.toml",
)

DEFAULT_REMOTE = "https://github.com/ProfessorZhi/serveros.git"


class ComposeError(RuntimeError):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def qa_dir() -> str:
    return str(Path(__file__).resolve().parent)


def _run(argv: list[str], *, timeout: float = 300.0) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, check=False, timeout=timeout)


def _has(repo: Path, sha: str) -> bool:
    return _run(["git", "-C", str(repo), "cat-file", "-e",
                 f"{sha}^{{commit}}"]).returncode == 0


def _extract(data: bytes, dest: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as bundle:
        bundle.extractall(dest)


def fetch_tree(ref: str, sha: str, dest: str | Path) -> Path:
    """Extract an exact commit, fetching a public ref when the checkout is shallow."""
    target = Path(dest)
    target.mkdir(parents=True, exist_ok=True)
    local = repo_root()
    if _has(local, sha):
        done = _run(["git", "-C", str(local), "archive", "--format=tar", sha])
        if done.returncode != 0:
            raise ComposeError(done.stderr.decode(errors="replace"))
        _extract(done.stdout, target)
        return target

    bare = target.parent / "w19.git"
    if not bare.exists():
        init = _run(["git", "init", "--bare", str(bare)])
        if init.returncode != 0:
            raise ComposeError(init.stderr.decode(errors="replace"))
    # Always fetch: main and #507 share the bare repo, and a skipped fetch
    # would leave FETCH_HEAD pointing at the previous ref (the Wave17 QA bug:
    # a shallow CI checkout got main's SHA for refs/pull/507/head).
    fetched = _run(["git", "-C", str(bare), "fetch", "--depth=1",
                    "--no-tags", DEFAULT_REMOTE, ref])
    if fetched.returncode != 0:
        raise ComposeError(fetched.stderr.decode(errors="replace")[-400:])
    head = _run(["git", "-C", str(bare), "rev-parse", "FETCH_HEAD"])
    got = head.stdout.decode().strip()
    if got != sha:
        raise ComposeError(f"{ref} moved: {got} != {sha}")
    archive = _run(["git", "--git-dir", str(bare), "archive", "--format=tar",
                    "FETCH_HEAD"])
    if archive.returncode != 0:
        raise ComposeError(archive.stderr.decode(errors="replace")[-400:])
    _extract(archive.stdout, target)
    return target


def main_tree(dest: str | Path | None = None) -> Path:
    return fetch_tree("refs/heads/main", MAIN,
                      dest or Path(tempfile.mkdtemp(prefix="w19-main-")))


def rc_tree(dest: str | Path | None = None) -> Path:
    return fetch_tree("refs/pull/507/head", RC,
                      dest or Path(tempfile.mkdtemp(prefix="w19-rc-")))


def baseline_500(dest: str | Path | None = None) -> Path:
    return fetch_tree("refs/pull/500/head", WAVE18,
                      dest or Path(tempfile.mkdtemp(prefix="w19-wave18-")))


def build_candidate(root: str | Path | None = None
                    ) -> tuple[Path, Path, Path, Path]:
    """Return (base, candidate, main, rc) trees.

    The candidate starts as the exact main tree, then each allowlisted
    integration directory and the runtime-tick test are replaced with #507's
    bytes. Nothing else is copied.
    """
    base = Path(root) if root else Path(tempfile.mkdtemp(prefix="w19-candidate-"))
    base.mkdir(parents=True, exist_ok=True)
    main = main_tree(base / "main")
    rc = rc_tree(base / "rc")
    candidate = base / "candidate"
    shutil.copytree(main, candidate)
    for relative in ALLOWED_DIRS:
        source = rc / relative
        if not source.is_dir():
            raise ComposeError(f"#507 has no {relative}")
        target = candidate / relative
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
    for relative in RUNTIME_TESTS:
        source = rc / relative
        if not source.is_file():
            raise ComposeError(f"#507 has no {relative}")
        target = candidate / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return base, candidate, main, rc


def file_map(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in root.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def delta_paths(left: Path, right: Path) -> list[str]:
    a, b = file_map(left), file_map(right)
    return sorted(path for path in set(a) | set(b) if a.get(path) != b.get(path))


def forbidden(paths: list[str]) -> list[str]:
    return sorted(path for path in paths
                  if any(path == prefix or path.startswith(prefix)
                         for prefix in FORBIDDEN_PREFIXES))


def allowed(path: str) -> bool:
    if path in RUNTIME_TESTS:
        return True
    return any(path == directory or path.startswith(directory + "/")
               for directory in ALLOWED_DIRS)


def source_paths(root: Path) -> list[str]:
    return [str(root / d) for d in ALLOWED_DIRS]


def install(root: Path) -> list[str]:
    paths = [qa_dir(), *source_paths(root)]
    for path in reversed(paths):
        if path not in sys.path:
            sys.path.insert(0, path)
    return paths


def remove(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
