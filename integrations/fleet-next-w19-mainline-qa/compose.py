"""W19 mainline QA: run the independent 43 assertions against current main.

The stack is now landed. This module no longer builds a #507 overlay fixture:

    44f039d (main, #521 landing ledger)
      <- b27c93e (#519 tests/docs/contract)
      <- 99c06fa (#520 monitor source)
      <- 3bc7adf (pre-landing Legacy coexistence)

`build_candidate()` returns the **current main tree** as both candidate and
main. If main does not carry the allowlisted monitor directories, it raises
`BLOCKED_SOURCE` rather than manufacturing a green tree. #507 is still fetched
at its exact SHA for the archive/veto comparison, but it is not copied over
main.

`fleet_mcp/`, `runtime/`, `plugin/`, `serverosd/`, `native/`, `plugins/`,
`fleet/`, `scripts/`, `.github/` and baseline files are never touched.
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

MAIN = "44f039d70dc4f593ea9db73aae873be3455fc48c"
RC = "05da04f8c78f302b029c6304ed96f4e8ded5fc9b"
QA508 = "fb8c435637e486fc0ecf78a2ea83b9b323c936bb"
WAVE18 = "80220a4ad1b009946f11bda0ce96dd8ff8e05468"

#: The three landed source batches this QA now measures.
LANDED = {
    520: "99c06fab07bd301e7df1ff7fa253f670b4d51d4d",
    519: "b27c93e346d5503a75f95ec068c386b416841a62",
    521: "44f039d70dc4f593ea9db73aae873be3455fc48c",
}

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

    Candidate == current main. The landed stack is measured as it actually is;
    a main tree without it is `BLOCKED_SOURCE`, not a fixture that overlays
    #507 and pretends the landing happened.
    """
    base = Path(root) if root else Path(tempfile.mkdtemp(prefix="w19-mainline-"))
    base.mkdir(parents=True, exist_ok=True)
    main = main_tree(base / "main")
    rc = rc_tree(base / "rc")
    candidate = base / "candidate"
    shutil.copytree(main, candidate)
    missing = [relative for relative in ALLOWED_DIRS
               if not (candidate / relative).is_dir()]
    if missing:
        raise ComposeError(
            "BLOCKED_SOURCE: current main does not carry the landed monitor "
            f"stack; missing {missing}")
    for relative in RUNTIME_TESTS:
        if not (candidate / relative).is_file():
            raise ComposeError(
                f"BLOCKED_SOURCE: current main has no {relative}; the #519 "
                "landing is incomplete")
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
