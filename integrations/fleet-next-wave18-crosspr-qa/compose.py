"""Wave 18 QA composition: the RC #500 plus the two follow-up fix commits.

    #500 80220a4 (Wave 16 RC: #497 + #498 + #499 on #494)
      -> #503 b7f1f61 (the three runtime-tick tests' ABI)
      -> #504 6286bfb (CI double grant, undecided != exit 0, delivery-id-only)

The merges are real ``git merge`` commits in the QA worktree. The author files
are pinned to their head blobs, so the tests prove the fixes are the ones under
review. The #500 tree is kept only as the RED baseline and as the source for
mutation controls; it is never put on ``sys.path`` for the main suite.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

PINS = {
    500: "80220a4ad1b009946f11bda0ce96dd8ff8e05468",
    503: "b7f1f6134a1e3dcee4b916068c693ff059c097bd",
    504: "6286bfbc885771a45415efcb33405bb21a841fde",
}

#: Fix file -> git blob at its author head. The composed tree must still carry
#: exactly these bytes.
FIX_BLOBS = {
    ("503", "tests/test_fleet_next_agent_monitor_activation_runtime_tick.py"):
        "a71d4ffce16913b537a383236e6955ea308d28b7",
    ("504", "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/contract.py"):
        "50d582b2259f682d24f3dfc4609de61804f25302",
    ("504", "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/core.py"):
        "33ce9aa01b91adb141d7cde2ff3c5e78e111297f",
    ("504", "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/notifier.py"):
        "6a0c16c4aa262d567050272e4ad5c50cc842b81b",
    ("504", "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/sources.py"):
        "d8a3a551e0b39322a9bb863c8e128acda9b04fd2",
}

INTEGRATION_DIRS = {
    493: "integrations/fleet-next-agent-monitor",
    488: "integrations/fleet-next-agent-monitor-activation",
    490: "integrations/fleet-next-monitor-source-adapter",
    423: "integrations/fleet-v1-milestone-notify",
}
BATCH_DIR = "integrations/fleet-v1-milestone-batch"
DEFAULT_REMOTE = "https://github.com/ProfessorZhi/serveros.git"


class ComposeError(RuntimeError):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def tree_root() -> Path:
    override = os.environ.get("FLEET_QA_BASELINE_ROOT")
    return Path(override).expanduser().resolve() if override else repo_root()


def source_paths(root: Path | None = None) -> list[str]:
    root = root or tree_root()
    return [str(root / INTEGRATION_DIRS[pr]) for pr in (423, 490, 493, 488)] + \
           [str(root / BATCH_DIR)]


def qa_dir() -> str:
    return str(Path(__file__).resolve().parent)


def install(root: Path | None = None) -> list[str]:
    paths = [qa_dir(), *source_paths(root)]
    for path in reversed(paths):
        if path not in sys.path:
            sys.path.insert(0, path)
    return paths


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _run(argv: list[str], *, timeout: float = 300.0) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, check=False, timeout=timeout)


def _has_commit(repo: Path, sha: str) -> bool:
    return _run(["git", "-C", str(repo), "cat-file", "-e",
                 f"{sha}^{{commit}}"]).returncode == 0


def _extract(data: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as bundle:
        bundle.extractall(destination)


def archive_commit(sha: str, destination: Path) -> Path:
    """Extract one commit's full tree, fetching the PR ref if necessary."""
    destination.mkdir(parents=True, exist_ok=True)
    local = repo_root()
    if _has_commit(local, sha):
        done = _run(["git", "-C", str(local), "archive", "--format=tar", sha])
        if done.returncode != 0:
            raise ComposeError(done.stderr.decode(errors="replace"))
        _extract(done.stdout, destination)
        return destination

    pr = next((number for number, pinned in PINS.items() if pinned == sha), None)
    if pr is None:
        raise ComposeError(f"no PR ref for unknown commit {sha}")
    bare = destination.parent / f"pr{pr}.git"
    if not bare.exists():
        init = _run(["git", "init", "--bare", str(bare)])
        if init.returncode != 0:
            raise ComposeError(init.stderr.decode(errors="replace"))
        fetched = _run(["git", "-C", str(bare), "fetch", "--depth=1",
                        "--no-tags", DEFAULT_REMOTE, f"refs/pull/{pr}/head"])
        if fetched.returncode != 0:
            raise ComposeError(fetched.stderr.decode(errors="replace")[-400:])
    head = _run(["git", "-C", str(bare), "rev-parse", "FETCH_HEAD"])
    got = head.stdout.decode().strip()
    if got != sha:
        raise ComposeError(f"PR #{pr} moved: {got} != {sha}")
    archive = _run(["git", "--git-dir", str(bare), "archive", "--format=tar",
                    "FETCH_HEAD"])
    if archive.returncode != 0:
        raise ComposeError(archive.stderr.decode(errors="replace")[-400:])
    _extract(archive.stdout, destination)
    return destination


def baseline_500(destination: str | Path | None = None) -> Path:
    target = Path(destination) if destination else Path(
        tempfile.mkdtemp(prefix="fleet-wave18-rc500-"))
    return archive_commit(PINS[500], target)


def archive_head(destination: str | Path) -> Path:
    """The composed tree at the QA commit, for mutation controls."""
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    done = _run(["git", "-C", str(repo_root()), "archive", "--format=tar", "HEAD"])
    if done.returncode != 0:
        raise ComposeError(done.stderr.decode(errors="replace"))
    _extract(done.stdout, target)
    return target


def remove(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
