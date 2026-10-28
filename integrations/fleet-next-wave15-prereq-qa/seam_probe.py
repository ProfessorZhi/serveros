#!/usr/bin/env python3
"""Ask #481's BatchAggregator the one question #495 answered with the wrong
notifier revision: does the notifier in front of it have a public
``send_text``?

WHY THIS FILE EXISTS
--------------------
#495's cross-PR QA pinned #423's *original* head ``d9b2948`` as the notifier
and reported ``NOTIFIER_SEAM_MISSING``. That head predates #460's public send
seam ``4fc9fcb``; the Wave 14 RC (``00503f9``) carries ``4fc9fcb`` through
#481's merge ``ba76a80``. The difference between those two statements is not a
source change, it is **which frozen commit was materialised**.

So this module does not reimplement or copy either notifier. It extracts each
pinned commit with ``git archive`` into a throw-away directory, puts one
revision in front of #481's real ``BatchAggregator``, and reports the outcome:

    old  (d9b2948)  -> send_text False -> NOTIFIER_SEAM_MISSING   (RED)
    new  (4fc9fcb)  -> send_text True  -> constructed              (GREEN)

Nothing is written into the repository. The only inputs are commits that are
already in the object database; a checkout without them must fail loudly
rather than silently measure a different tree.

WHAT THIS DOES NOT DO
---------------------
No network, no provider call, no credential, no service, no Feishu/IM, no
model, no GPU. ``Provider`` is a placeholder; constructing ``MilestoneNotifier``
does not send. LAYERS: CODE (git objects) / UNIT (unittest via the test file).
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

#: The Wave 14 RC under test.
CURRENT = "00503f91b019ce342af52f38b19bb3aa21dfff55"

#: #423's original head, which #495 pinned and which has no send_text.
OLD_423 = "d9b2948a62609e16007f2fe0ae288f9652d64cd1"

#: #460's public send seam. It is an ancestor of the RC through #481.
SEAM_460 = "4fc9fcbc677f278d10caeb34d55184545810a13a"

#: #460's final head: the seam plus the newline fix. NOT an ancestor of the RC.
NEWLINE_460 = "1dbeda5f87bce23b2134f869f6379667bc9847b9"

#: #481's Batch CLI, which merged SEAM_460 at ba76a80.
BATCH_481 = "9b7710a26180e1e6d00ce597827d447d0d2348b7"

#: #488's activation/batch adapter head, which sits on #481.
ACTIVATION_488 = "009662f36a2a5f8e11c51ae517834379a91a7770"

NOTIFIER_DIR = "integrations/fleet-v1-milestone-notify"
BATCH_DIR = "integrations/fleet-v1-milestone-batch"
OBSERVER_DIR = "integrations/fleet-v1-milestone-observer"
SHARED_DIRS = (BATCH_DIR, OBSERVER_DIR, "runtime")


class ProbeError(RuntimeError):
    """The probe could not materialise the pinned commits it claims to read."""


def _archive(repo: str | Path, commit: str, paths: tuple[str, ...],
             destination: Path) -> None:
    done = subprocess.run(
        ("git", "-C", str(repo), "archive", "--format=tar", commit, *paths),
        capture_output=True, check=False, timeout=120)
    if done.returncode != 0:
        raise ProbeError(
            f"git archive {commit} failed: "
            f"{done.stderr.decode(errors='replace')[-400:]}; the checkout may "
            "be shallow — the hosted suite uses fetch-depth: 0 for this reason")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(done.stdout), mode="r:") as bundle:
        try:
            bundle.extractall(destination, filter="data")
        except TypeError:  # Python < 3.12, kept for the local runner
            bundle.extractall(destination)


def prepare(repo: str | Path, root: str | Path) -> Path:
    """Extract the two notifier revisions beside the RC's Batch CLI.

    ``old`` and ``new`` each get their own notifier directory and the *same*
    current Batch/observer/runtime, so the only variable is the notifier.
    """
    root = Path(root)
    for mode, commit in (("old", OLD_423), ("new", SEAM_460)):
        destination = root / mode
        _archive(repo, commit, (NOTIFIER_DIR,), destination)
        _archive(repo, CURRENT, SHARED_DIRS, destination)
    return root


def probe(root: str | Path, mode: str) -> dict:
    """Construct #481's real aggregator with one notifier revision."""
    destination = Path(root) / mode
    sys.path.insert(0, str(destination / BATCH_DIR))
    sys.path.insert(0, str(destination / NOTIFIER_DIR))
    import milestone_notify  # noqa: PLC0415 - deliberately after sys.path
    import batch  # noqa: PLC0415

    class Provider:
        """Construction does not send; any call would be a test defect."""

    ledger = Path(tempfile.mkdtemp(prefix="wave15-seam-ledger-")) / "ledger.sqlite"
    notifier = milestone_notify.MilestoneNotifier(
        Provider(), ledger, conversation_ref="chat:wave15-qa")

    result = {
        "mode": mode,
        "send_text": callable(getattr(notifier, "send_text", None)),
        "constructed": False,
        "code": None,
        "why": None,
    }
    member = batch.BatchMember(
        source="cloudcli", ref=None, device_ref="device:wave15-qa",
        task_id="task-1")
    declaration = batch.Batch(
        batch_id="wave15-qa-batch", members=(member,),
        expires_at="2026-10-30T00:00:00+00:00")
    try:
        batch.BatchAggregator(declaration, notifier=notifier)
        result["constructed"] = True
    except Exception as exc:  # the refusal is the observation
        result["code"] = getattr(exc, "code", type(exc).__name__)
        result["why"] = str(exc)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo")
    parser.add_argument("--root", required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--mode", choices=("old", "new"))
    args = parser.parse_args(argv)
    if args.prepare:
        if not args.repo:
            parser.error("--prepare requires --repo")
        prepare(args.repo, args.root)
        print(json.dumps({"prepared": str(Path(args.root).resolve())}))
        return 0
    if not args.mode:
        parser.error("one of --prepare or --mode is required")
    print(json.dumps(probe(args.root, args.mode), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
