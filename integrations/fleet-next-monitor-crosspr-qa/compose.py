"""Materialise the four pinned PR trees, so the QA tests exercise real code.

Why this exists
---------------
The four PRs under acceptance are not on ``main`` and are not on one branch:

```
#493  feat/fleet-next-agent-monitor-core-20261028    @ 4f5320d
#488  feat/next-monitor-activation-batch-cli         @ 009662f   (base: #481 chain)
#490  feat/fleet-next-monitor-source-adapter-20261028@ 06a8be6
#423  feat/fleet-v1-milestone-notify-20261027        @ d9b2948
```

A test that imported the nearest working tree would prove whichever branch
happened to be checked out, not the combination. A test that copied snippets
into fixtures would prove the snippets. So this module extracts **each exact
commit** from git and puts the four integrations on ``sys.path`` side by side.
Nothing is edited, rewritten or selected by hand.

Local runs use the object database of the checkout the test lives in. CI runs
on a shallow checkout that does not contain the four commits, so the same
function fetches ``refs/pull/<n>/head`` into a throw-away bare repository and
then *asserts* the fetched commit equals the pinned SHA. A drifted PR is a
named failure, not a silent newer tree.

Baseline
--------
``FLEET_QA_BASELINE_ROOT=/path/to/pristine/main`` makes this module return that
tree for every PR. The tests then fail with the monitor package absent — the
before picture, recorded rather than assumed.
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

#: The exact heads named in the dispatch. These are the inputs; a QA run that
#: substituted a branch tip would be measuring a different question.
PINNED = {
    493: "4f5320de7e187daadd79adc92e7487facd7b1820",
    488: "009662f36a2a5f8e11c51ae517834379a91a7770",
    490: "06a8be67422f1320e7bdfee38410f8b24d27b501",
    423: "d9b2948a62609e16007f2fe0ae288f9652d64cd1",
}

#: Where each integration package lives inside a checkout.
INTEGRATION_DIRS = {
    493: "integrations/fleet-next-agent-monitor",
    488: "integrations/fleet-next-agent-monitor-activation",
    490: "integrations/fleet-next-monitor-source-adapter",
    423: "integrations/fleet-v1-milestone-notify",
}

#: The Batch lane's own directory, from #488's base chain (#481).
BATCH_DIR = "integrations/fleet-v1-milestone-batch"

DEFAULT_REMOTE = "https://github.com/ProfessorZhi/serveros.git"


class ComposeError(RuntimeError):
    """The pinned combination could not be materialised."""


def repo_root() -> Path:
    """The checkout this QA file belongs to (the QA worktree, not another)."""
    return Path(__file__).resolve().parents[2]


def _run(argv: list[str], *, cwd: str | Path | None = None,
         timeout: float = 180.0) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=str(cwd) if cwd else None, timeout=timeout,
                          capture_output=True, check=False)


def _has_commit(repo: Path, sha: str) -> bool:
    done = _run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"])
    return done.returncode == 0


def _extract(archive: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        bundle.extractall(destination)


def _archive_local(repo: Path, sha: str, destination: Path) -> None:
    done = _run(["git", "-C", str(repo), "archive", "--format=tar", sha])
    if done.returncode != 0:
        raise ComposeError(f"git archive {sha} failed: {done.stderr.decode(errors='replace')}")
    _extract(done.stdout, destination)


def _archive_remote(pr: int, sha: str, destination: Path,
                    remote: str) -> None:
    bare = destination.parent / f"pull-{pr}.git"
    init = _run(["git", "init", "--bare", str(bare)])
    if init.returncode != 0:
        raise ComposeError(f"git init {bare} failed: {init.stderr.decode(errors='replace')}")
    fetched = _run(["git", "-C", str(bare), "fetch", "--depth=1", "--no-tags",
                    remote, f"refs/pull/{pr}/head"], timeout=300.0)
    if fetched.returncode != 0:
        raise ComposeError(
            f"cannot fetch refs/pull/{pr}/head from {remote}: "
            f"{fetched.stderr.decode(errors='replace')[-400:]}")
    head = _run(["git", "-C", str(bare), "rev-parse", "FETCH_HEAD"])
    got = head.stdout.decode().strip()
    if got != sha:
        raise ComposeError(
            f"PR #{pr} moved: refs/pull/{pr}/head is {got}, the dispatch pinned {sha}")
    archive = _run(["git", "--git-dir", str(bare), "archive", "--format=tar",
                    "FETCH_HEAD"])
    if archive.returncode != 0:
        raise ComposeError(
            f"git archive of PR #{pr} failed: "
            f"{archive.stderr.decode(errors='replace')[-400:]}")
    _extract(archive.stdout, destination)


def materialize(root: str | Path | None = None) -> tuple[Path, dict[int, Path]]:
    """Extract each pinned PR into its own directory.

    Returns ``(root, {pr: checkout})``. ``root`` is the temporary parent; it is
    owned by the caller, which may remove it at the end of the run.
    """
    baseline = os.environ.get("FLEET_QA_BASELINE_ROOT")
    if baseline:
        pinned_root = Path(baseline).expanduser().resolve()
        if not pinned_root.is_dir():
            raise ComposeError(f"FLEET_QA_BASELINE_ROOT {pinned_root} is not a directory")
        return pinned_root, {pr: pinned_root for pr in PINNED}

    destination = Path(root) if root else Path(
        tempfile.mkdtemp(prefix="fleet-crosspr-qa-"))
    destination.mkdir(parents=True, exist_ok=True)
    local = repo_root()
    checkouts: dict[int, Path] = {}
    for pr, sha in PINNED.items():
        target = destination / f"pr{pr}"
        target.mkdir(parents=True, exist_ok=True)
        if _has_commit(local, sha):
            _archive_local(local, sha, target)
        else:
            _archive_remote(pr, sha, target, os.environ.get(
                "FLEET_QA_REMOTE", DEFAULT_REMOTE))
        checkouts[pr] = target
    return destination, checkouts


def integration_paths(checkouts: dict[int, Path]) -> list[str]:
    """The four integration roots, plus the Batch lane, in import order.

    Order matters in exactly one place: only #423's directory is added for the
    module name ``milestone_notify``. #488's tree carries its own divergent copy
    of that file; putting it on ``sys.path`` would silently choose a different
    revision of the notifier than the one the dispatch pinned.
    """
    paths: list[str] = []
    for pr in (423, 490, 493, 488):
        root = checkouts[pr] / INTEGRATION_DIRS[pr]
        paths.append(str(root))
    paths.append(str(checkouts[488] / BATCH_DIR))
    return paths


def qa_dir() -> str:
    return str(Path(__file__).resolve().parent)


def install(checkouts: dict[int, Path]) -> list[str]:
    """Put the composition on ``sys.path`` and return the paths added."""
    paths = [qa_dir(), *integration_paths(checkouts)]
    for path in reversed(paths):
        if path not in sys.path:
            sys.path.insert(0, path)
    return paths


def remove(root: Path) -> None:
    shutil.rmtree(root, ignore_errors=True)
