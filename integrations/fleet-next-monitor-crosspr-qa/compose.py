"""Materialise the real Wave 14 RC and the historical notifier pin.

Why the baseline moved
----------------------
#495 v1 composed four per-PR heads and pinned #423 at ``d9b2948``. That head has
no ``send_text``, so the Batch lane correctly refused it with
``NOTIFIER_SEAM_MISSING`` — but the refusal was a **pin artefact**, not a defect
in the composed product. Wave 14 RC #494 at ``00503f9`` carries #460's public
seam ``4fc9fcb`` through #481's merge ``ba76a80``; its notifier blob is
``7df27cb``. The old pin's blob is ``72fcec8``. The RC also carries #493, #488
and #490 at the exact commits #495 pinned, with byte-identical blobs.

So the code under test is now the **#494 tree**, and the old #423 head is kept
only as a historical control. A test that still expected
``NOTIFIER_SEAM_MISSING`` from the RC would be measuring the old pin again; it
is removed, and a RED→GREEN control proves the difference instead.

CI
--
Local runs use the object database of the checkout this QA file lives in. CI
runs on a shallow checkout, so the same function fetches
``refs/pull/<n>/head`` into a throw-away bare repository and asserts the fetched
commit equals the pinned SHA. A drifted PR is a named failure, not a silent
newer tree.

Baseline
--------
``FLEET_QA_BASELINE_ROOT=/path/to/pristine/main`` returns that tree for the RC
and disables the historical control. The tests then fail with the monitor
package absent — the before picture, recorded rather than assumed.
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

#: The real Wave 14 RC. Its tree is the composition under test.
RC_PR = 494
RC = "00503f91b019ce342af52f38b19bb3aa21dfff55"

#: The author commits the RC carries. #493/#488/#490 are byte-identical in the
#: RC (asserted by the test); #423 is the *historical* pin #495 v1 used.
PINNED = {
    RC_PR: RC,
    493: "4f5320de7e187daadd79adc92e7487facd7b1820",
    488: "009662f36a2a5f8e11c51ae517834379a91a7770",
    490: "06a8be67422f1320e7bdfee38410f8b24d27b501",
    423: "d9b2948a62609e16007f2fe0ae288f9652d64cd1",
}

#: The notifier the RC actually carries, through #460 → #481.
COMPOSED_NOTIFIER = "4fc9fcbc677f278d10caeb34d55184545810a13a"
COMPOSED_NOTIFIER_BLOB = "7df27cb36b169134ff279e43c0b9c329cf919fef"
HISTORICAL_423_BLOB = "72fcec8d553f1568a114449d352e1803250d26cc"

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


def _fetch_tree(pr: int, sha: str, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    local = repo_root()
    if _has_commit(local, sha):
        _archive_local(local, sha, target)
    else:
        _archive_remote(pr, sha, target, os.environ.get(
            "FLEET_QA_REMOTE", DEFAULT_REMOTE))


def materialize(root: str | Path | None = None
                ) -> tuple[Path, Path, Path | None]:
    """Extract the RC and the historical #423 pin.

    Returns ``(root, rc_tree, historical_423_tree)``. The historical tree is
    ``None`` in a baseline run; it exists only to keep the pin-artefact
    correction falsifiable (the old file must still lack ``send_text``).
    """
    baseline = os.environ.get("FLEET_QA_BASELINE_ROOT")
    if baseline:
        pinned_root = Path(baseline).expanduser().resolve()
        if not pinned_root.is_dir():
            raise ComposeError(f"FLEET_QA_BASELINE_ROOT {pinned_root} is not a directory")
        return pinned_root, pinned_root, None

    destination = Path(root) if root else Path(
        tempfile.mkdtemp(prefix="fleet-crosspr-qa-"))
    destination.mkdir(parents=True, exist_ok=True)
    rc_tree = destination / f"rc{RC_PR}"
    _fetch_tree(RC_PR, RC, rc_tree)
    historical = destination / "historical-423"
    _fetch_tree(423, PINNED[423], historical)
    return destination, rc_tree, historical


def integration_paths(rc_tree: Path) -> list[str]:
    """The four integration roots, plus the Batch lane, in import order.

    Order matters in exactly one place: only the RC's directory is added for
    the module name ``milestone_notify``. The historical #423 tree is read as
    text by the control test, never put on ``sys.path``.
    """
    paths: list[str] = []
    for pr in (423, 490, 493, 488):
        paths.append(str(rc_tree / INTEGRATION_DIRS[pr]))
    paths.append(str(rc_tree / BATCH_DIR))
    return paths


def qa_dir() -> str:
    return str(Path(__file__).resolve().parent)


def install(rc_tree: Path) -> list[str]:
    """Put the composition on ``sys.path`` and return the paths added."""
    paths = [qa_dir(), *integration_paths(rc_tree)]
    for path in reversed(paths):
        if path not in sys.path:
            sys.path.insert(0, path)
    return paths


def remove(root: Path) -> None:
    shutil.rmtree(root, ignore_errors=True)
