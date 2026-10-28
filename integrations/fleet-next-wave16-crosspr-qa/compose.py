"""The Wave 16 QA tree: RC #494 composed with the three fix commits.

The composition is made by **real git merges** in the QA worktree, not by
copying snippets:

    00503f9 (#494 RC)
      -> 3db8ebf (#497 monitor receipt / typed CI subject)
      -> 0096ba6 (#498 activation verdict)
      -> 02027c5 (#499 source-adapter compat view)

Those three merges are clean (`git merge-tree` was checked before the
worktree was created). The QA files are added on top and touch none of the
authors' paths.

This module gives the tests one place to resolve the tree and to name the
exact author blobs. A test that imported whatever happened to be checked out
would not prove the three fixes are the ones under review; the blob check
does.
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

#: The four exact commits in the composition. #494 is the base; the other three
#: are the fix heads the dispatch named.
PINS = {
    494: "00503f91b019ce342af52f38b19bb3aa21dfff55",
    497: "3db8ebf107f83981965cc888269b3b90d89ecb72",
    498: "0096ba695f6c7bcd35dbb5a82957a1f5631d9058",
    499: "02027c53f59100fbf43e0ac1f6d84f58561ee91c",
}

#: `path -> git blob id` at the fix head. The composed tree must still carry
#: exactly these bytes, so "the fix is in" is a property of the file, not of a
#: commit message.
FIX_BLOBS = {
    ("497", "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/core.py"):
        "f95e9a8e111e128d9097eb903e4f1337139d3474",
    ("497", "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/notifier.py"):
        "b29122e32222680c993628f10a7d676a48aa960f",
    ("497", "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/state.py"):
        "8ff084be05b49daa164cca0ad981259ebd36f948",
    ("498", "integrations/fleet-next-agent-monitor-activation/activation.py"):
        "200452f10f09a2baf99641c5bf4f9548eef2e4bc",
    ("499", "integrations/fleet-next-monitor-source-adapter/fleet_next_monitor_source_adapter/adapter.py"):
        "28ad9baa6262480015f3907435647cbb48ed2e0f",
    ("499", "integrations/fleet-next-monitor-source-adapter/fleet_next_monitor_source_adapter/compat.py"):
        "f14272e71afee96f86679c63dde0f359e517be74",
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
    """The tree under test: the baseline override, else this QA worktree."""
    override = os.environ.get("FLEET_QA_BASELINE_ROOT")
    return Path(override).expanduser().resolve() if override else repo_root()


def source_paths(root: Path | None = None) -> list[str]:
    root = root or tree_root()
    paths = [str(root / INTEGRATION_DIRS[pr]) for pr in (423, 490, 493, 488)]
    paths.append(str(root / BATCH_DIR))
    return paths


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


def _extract_archive(data: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as bundle:
        bundle.extractall(destination)


def materialize_rc(destination: str | Path | None = None) -> Path:
    """Extract #494 for the RED baseline probe.

    A local checkout may already have the commit; CI's shallow checkout does
    not, so the same path fetches ``refs/pull/494/head`` and asserts the SHA.
    """
    target = Path(destination) if destination else Path(
        tempfile.mkdtemp(prefix="fleet-wave16-rc494-"))
    target.mkdir(parents=True, exist_ok=True)
    local = repo_root()
    if _has_commit(local, PINS[494]):
        done = _run(["git", "-C", str(local), "archive", "--format=tar",
                     PINS[494]])
        if done.returncode != 0:
            raise ComposeError(done.stderr.decode(errors="replace"))
        _extract_archive(done.stdout, target)
        return target

    bare = target.parent / "rc494.git"
    if not bare.exists():
        init = _run(["git", "init", "--bare", str(bare)])
        if init.returncode != 0:
            raise ComposeError(init.stderr.decode(errors="replace"))
        fetched = _run(["git", "-C", str(bare), "fetch", "--depth=1",
                        "--no-tags", DEFAULT_REMOTE, "refs/pull/494/head"])
        if fetched.returncode != 0:
            raise ComposeError(fetched.stderr.decode(errors="replace")[-400:])
    head = _run(["git", "-C", str(bare), "rev-parse", "FETCH_HEAD"])
    got = head.stdout.decode().strip()
    if got != PINS[494]:
        raise ComposeError(f"PR #494 moved: {got} != {PINS[494]}")
    archive = _run(["git", "--git-dir", str(bare), "archive", "--format=tar",
                    "FETCH_HEAD"])
    if archive.returncode != 0:
        raise ComposeError(archive.stderr.decode(errors="replace")[-400:])
    _extract_archive(archive.stdout, target)
    return target


def remove(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
