#!/usr/bin/env python3
"""A live checkout, asked in a form a fixture can afford to hear "no" from.

Several suites here exercise a `records` checkout that lives on one developer
node. The guard was written as

    @pytest.mark.skipif(not LIVE_RECORDS.is_dir(), reason="records 检出不在本机")

and `Path.is_dir()` has two answers where the guard needs three: it says False
when the path does not exist, and it RAISES `PermissionError` when the path
exists under another user's home — which is what a second account on the same
machine sees. Measured 2026-10-23 on a node where `/data/home/vince` belongs to
`vince` and the suite runs as `fjd`: eight modules became collection or setup
errors, and a gate that should have said "not on this node" reported instead
that the file it guards is broken.

`readable_dir` keeps the three answers: present and lookable, absent, and
present but not lookable. The last two are both "this node cannot supply the
fixture", which is what the skip is for.
"""
from __future__ import annotations

import os
from pathlib import Path

#: Opt-in: "this machine is the machine whose contents you are reporting."
LIVE_NODE_ENV = "SERVEROS_LIVE_NODE"


def readable_dir(path: Path) -> bool:
    """True only when `path` is a directory this process can look inside."""
    try:
        return path.is_dir()
    except OSError:
        return False


def live_node() -> bool:
    """Whether this process may report on the machine's REAL roots.

    A bound root is not the same fact as a real machine. `.github/scripts/
    hosted_daemon_inputs.py` binds a synthetic node so that several hundred
    tests can answer at all, and on that node `{DATA_ROOT}` exists and is
    empty — so a guard written as `DATA_ROOT.is_dir()` lets a report about "how
    many retained artifacts are here" run against nothing and call the answer
    zero. Those tests are opt-in instead of inferred: the variable is set on a
    node somebody has identified as the machine, and its absence SKIPS them
    with this message.
    """
    return os.environ.get(LIVE_NODE_ENV) == "1"
