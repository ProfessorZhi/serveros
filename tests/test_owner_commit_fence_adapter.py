"""The cross-platform half of the owner commit-fence gate.

This is the adapter-only assertion split out of
`tests/test_owner_commit_fence.py`, so it can run on Windows where the
daemon-based tests cannot be collected. It does not import `runtime/daemon.py`,
AF_UNIX, `SO_PEERCRED`, `fcntl` or `os.getuid()`; the Linux-only peer/socket
tests stay in the POSIX module.

The owner-backed answer is a canned parent reply. The adapter must refuse a
positive answer that carries owner markers without `atomicity: "COMMIT_FENCED"`;
no lease store is involved at this layer.
"""
from __future__ import annotations

import time

import pytest

from workengine.authority import AuthorityError
from workengine.serveros_authority import ServerOSLeaseAuthority

ACTION = "lease.acquire"
PROJECT = "serveros"
ITEM = "SRV-0001"


class _AnswerClient:
    """A canned verify answer, for the adapter's own gate."""

    def __init__(self, answer):
        self.answer = answer

    def verify(self, context_ref, *, grant_ref, action, project, item_ref):
        return self.answer


def _grant_payload(*, ref="grant:1") -> dict:
    # A fixed principal string: the adapter only compares it with the bound
    # grant, so this test needs no OS uid and runs on Windows.
    return {
        "principal": "human:A", "grant_ref": ref,
        "os_principal": "uid:4242", "epoch": 1,
        "valid_from": "2000-01-01T00:00:00+00:00",
        "expires_at": "2999-01-01T00:00:00+00:00", "revoked": False,
    }


def test_the_adapter_refuses_an_owner_backed_positive_without_commit_fence():
    answer = {
        "verified": True,
        "grant": _grant_payload(),
        "owner_policy": {"policy_ref": "policy:test", "policy_epoch": 1},
        "point_in_time": True,
        "atomicity": "BLOCKED_OWNER_COMMIT_SEAM",
    }
    authority = ServerOSLeaseAuthority(_AnswerClient(answer),
                                       grant_ref="grant:1")
    with pytest.raises(AuthorityError) as caught:
        authority.verify("context:test", action=ACTION, project=PROJECT,
                         item_ref=ITEM, now=time.time())
    assert caught.value.reason_code == "BLOCKED_OWNER_COMMIT_SEAM", \
        str(caught.value)
