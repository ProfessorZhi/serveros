"""Grant windows are INTERVALS, not strings (#237 B3).

WHY THIS FILE EXISTS
--------------------
`verify_live_grant` is the trusted-parent read that Work Engine's
`OwnerVerifiedLease` consumes. Before this hardening it compared the grant's
`valid_from` / `expires_at` to the verification instant as TEXT:

    if str(grant["valid_from"]) > now: ...
    if str(grant["expires_at"]) <= now: ...

Two instants only sort correctly as text when they use the same timezone
spelling and the same precision. A grant expiring at `12:00+08:00` is the same
instant as `04:00Z`, but `"12:00+08:00"` sorts ABOVE `"04:00+00:00"`, so an
EXPIRED grant verified for as long as the offset's hour was ahead of UTC. The
same comparison treated `...:00Z` and `...:00+00:00` as different instants, and
an unreadable window (`not-a-time`) sorted above every real instant.

The store already had `_instant()`, which parses a zoned timestamp or returns
None. This file pins that the authority read uses it, and that an unreadable
instant is a refusal rather than a comparison that happens to be false.

LEVEL: LOCAL / HERMETIC. Pure function, no daemon, no socket, no network.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# `verify_live_grant` loads `context/actor.py` under this exact name.
_actor = _load("_serveros_context_actor", REPO / "context" / "actor.py")
_store = _load("serveros_runtime_agent_control_store_s1",
               REPO / "runtime" / "agent_control_store.py")

PROJECT = "serveros"
ITEM = "SRV-0001"
ACTION = "lease.acquire"
NOW = "2026-10-20T04:00:00+00:00"


def a_context(now: str = NOW, *, actor_ref: str = "human:A") -> dict:
    return _actor.build({
        "human_control_provider": "provider:test",
        "transport": "feishu",
        "transport_identity": "test",
        "conversation_ref": "oc_grant_window",
        "message_ref": "om_grant_window",
        "observed_at": now,
        "provider_status": "VERIFIED",
        "provenance": "FACT",
        "actor_ref": actor_ref,
        "provider_pid": os.getpid(),
    }, now=now)


def a_grant(context: dict, *, valid_from="2026-10-20T00:00:00+00:00",
            expires_at="2026-10-20T05:00:00+00:00", **overrides) -> dict:
    record = {
        "grant_ref": "grant:window",
        "delegated_by": context["context_ref"],
        "valid_from": valid_from,
        "expires_at": expires_at,
        "projects": [PROJECT],
        "items": [ITEM],
        "actions": [ACTION],
        "revoked": False,
        "epoch": 1,
    }
    record.update(overrides)
    return record


def verify(record: dict, *, now: str = NOW, context: dict | None = None,
           action: str = ACTION, project: str = PROJECT,
           item_ref: str = ITEM, expected_epoch=None,
           peer: dict | None = None) -> dict:
    context = context if context is not None else a_context(now)
    return _store.verify_live_grant(
        {record["grant_ref"]: record},
        grant_ref=record["grant_ref"],
        context=context,
        peer=peer if peer is not None else {
            "pid": os.getpid(), "uid": os.getuid(), "gid": os.getgid()},
        action=action,
        project=project,
        item_ref=item_ref,
        expected_epoch=expected_epoch,
        now=now,
    )


class GrantWindowAuthorityTest(unittest.TestCase):
    # -- the non-UTC fail-open -------------------------------------------

    def test_an_expired_non_utc_grant_is_refused(self) -> None:
        now = "2026-10-20T04:00:01+00:00"
        context = a_context(now)
        # 12:00+08:00 is 04:00Z: one second past its expiry.
        record = a_grant(context, expires_at="2026-10-20T12:00:00+08:00")
        answer = verify(record, now=now, context=context)
        self.assertIs(answer["verified"], False, answer)
        self.assertEqual(answer["reason"], "POLICY_DENIED")

    def test_a_not_yet_valid_non_utc_grant_is_refused(self) -> None:
        now = "2026-10-20T03:59:59+00:00"
        context = a_context(now)
        # 12:00+08:00 is 04:00Z: one second before it becomes valid.
        record = a_grant(context, valid_from="2026-10-20T12:00:00+08:00")
        answer = verify(record, now=now, context=context)
        self.assertIs(answer["verified"], False, answer)
        self.assertEqual(answer["reason"], "POLICY_DENIED")

    def test_z_and_the_utc_offset_are_the_same_instant(self) -> None:
        now = "2026-10-20T04:00:00+00:00"
        context = a_context(now)
        record = a_grant(context, expires_at="2026-10-20T04:00:00Z")
        answer = verify(record, now=now, context=context)
        self.assertIs(answer["verified"], False, answer)
        self.assertEqual(answer["reason"], "POLICY_DENIED",
                         "an expiry at the current instant is not a live grant")

    # -- unreadable instants ---------------------------------------------

    def test_an_unreadable_window_is_refused(self) -> None:
        now = NOW
        context = a_context(now)
        for bad in ("not-a-time", "2026-10-20T04:00:00", "", None):
            with self.subTest(expires_at=bad):
                record = a_grant(context, expires_at=bad)
                answer = verify(record, now=now, context=context)
                self.assertIs(answer["verified"], False, answer)
                self.assertEqual(answer["reason"], "INVALID_CONTEXT")

    def test_an_unreadable_verification_instant_is_refused(self) -> None:
        context = a_context(NOW)
        record = a_grant(context)
        for bad in ("not-a-time", "2026-10-20T04:00:00", "", None):
            with self.subTest(now=bad):
                answer = verify(record, now=bad, context=context)
                self.assertIs(answer["verified"], False, answer)
                self.assertEqual(answer["reason"], "INVALID_CONTEXT")

    # -- the positive control --------------------------------------------

    def test_a_live_grant_with_an_offset_window_still_verifies(self) -> None:
        now = "2026-10-20T04:00:00+00:00"
        context = a_context(now)
        # 11:00+08:00 = 03:00Z; 13:00+08:00 = 05:00Z; now is inside.
        record = a_grant(context,
                         valid_from="2026-10-20T11:00:00+08:00",
                         expires_at="2026-10-20T13:00:00+08:00")
        answer = verify(record, now=now, context=context)
        self.assertIs(answer["verified"], True, answer)
        self.assertEqual(answer["grant"]["principal"], "human:A")

    # -- the read is pure -------------------------------------------------

    def test_the_read_mutates_neither_the_grant_nor_the_context(self) -> None:
        now = NOW
        context = a_context(now)
        record = a_grant(context)
        grants = {record["grant_ref"]: dict(record)}
        before_context = dict(context)
        answer = verify(record, now=now, context=context)
        self.assertIs(answer["verified"], True, answer)
        self.assertEqual(grants[record["grant_ref"]], record)
        self.assertEqual(context, before_context)


if __name__ == "__main__":
    unittest.main()
