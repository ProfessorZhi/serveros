#!/usr/bin/env python3
"""Apply the Wave 17 owner handoff to a SANDBOX COPY, never to the repository.

This module exists so the RED -> GREEN proof can be re-run by anyone:

    CUT-A  the three `FleetService` helper delegations (#461's authored change,
           mechanically rebased onto `main` at e69fc03 for this proof);
    CUT-B  the Hub -> Node optional seam and the provider-barrel split, which
           is what this lane asks the `fleet_mcp/**` owner to land.

Two rules are structural, not advisory:

* **The target must be a copy.** A directory containing `.git` is refused, and
  so is any path inside this repository. A handoff that can be pointed at the
  checkout it is meant to describe is a handoff that will eventually be run
  against it by accident.
* **The patch files are the contract.** `apply()` runs `git apply` over the
  two `.patch` files shipped beside this module. It does not re-implement the
  edits in Python, because a second implementation of the same change is a
  second thing that can drift from the one the owner reviews.

Nothing here installs, starts, connects to or reads a credential.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PATCHES = ("cut_a_main_rebase.patch", "cut_b_hub_node_seam.patch")


class HandoffRefused(RuntimeError):
    """The target is not a sandbox copy."""


def _repo_root() -> Path:
    # handoff/ -> fleet-next-wave17-hub-node-release-cut/ -> integrations/ -> repo
    return Path(__file__).resolve().parents[3]


def guard(root: Path) -> Path:
    """Refuse anything that is not a throwaway copy of the source tree."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise HandoffRefused(f"{root} is not a directory")
    if (root / ".git").exists():
        raise HandoffRefused(
            f"{root} contains .git; this handoff applies only to a copy made "
            f"with `git archive`, never to a checkout")
    repo = _repo_root()
    if root == repo or repo in root.parents:
        raise HandoffRefused(
            f"{root} is inside this repository ({repo}); the owner's files are "
            f"read-only in this lane")
    if not (root / "fleet_mcp/node/connector.py").is_file():
        raise HandoffRefused(
            f"{root} does not look like a serveros source tree: "
            f"fleet_mcp/node/connector.py is absent")
    return root


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=check,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin",
             "GIT_AUTHOR_NAME": "wave17-handoff",
             "GIT_AUTHOR_EMAIL": "wave17@invalid",
             "GIT_COMMITTER_NAME": "wave17-handoff",
             "GIT_COMMITTER_EMAIL": "wave17@invalid",
             # A fixed date makes the sandbox commit id a function of the tree,
             # so the evidence file names the same measured tree on every run.
             "GIT_AUTHOR_DATE": "2026-10-28T00:00:00+0000",
             "GIT_COMMITTER_DATE": "2026-10-28T00:00:00+0000",
             "HOME": str(root)})


def apply(root: Path) -> dict:
    """Apply both patches to `root` and return what happened, by name."""
    root = guard(root)
    here = Path(__file__).resolve().parent
    # A repository is created only so `git apply` can report a precise failure.
    # It is local to the sandbox copy and disappears with it.
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "wave17 sandbox base")
    applied = []
    for name in PATCHES:
        patch = here / name
        if not patch.is_file():
            raise HandoffRefused(f"the handoff patch {name} is missing from {here}")
        checked = _git(root, "apply", "--check", str(patch), check=False)
        if checked.returncode != 0:
            raise HandoffRefused(
                f"{name} does not apply to {root}: {checked.stderr.strip()[:300]}")
        _git(root, "apply", str(patch))
        applied.append(name)
    return {"root": str(root), "patches": applied}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True,
                        help="a copy of the source tree (no .git) to patch")
    args = parser.parse_args(argv)
    try:
        print(apply(Path(args.root)))
    except HandoffRefused as exc:
        sys.stderr.write(f"refused: {exc}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
