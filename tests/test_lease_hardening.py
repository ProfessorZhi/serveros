"""P0 hardening for the exclusive Work write lease (#237 B3 / T2, T3, T6).

WHY THIS FILE EXISTS
--------------------
`tests/test_lease.py` proves the happy path and the three refusals the lease was
built for. This file covers the failure modes that sit BETWEEN those facts, and
each one is a way a lease can authorize something it should not:

  * a TTL of zero, a negative TTL, NaN, Infinity, a non-number, or an absurd
    value. Zero/negative used to be WRITTEN as an already-expired row and the
    acquire returned normally; NaN/Infinity raised raw `ValueError`/
    `OverflowError` from `math.ceil`, which is neither a refusal word nor a
    rolled-back write.
  * a NaN or infinite CLOCK. `expires_at > NaN` is always false, so a caller
    that supplied NaN as `now` could take over a LIVE lease. A non-finite clock
    is now a refusal before the transaction.
  * a malformed `expires_at` row. It must be a typed refusal, never "cannot be
    aged, therefore expired, therefore free to take over".
  * a token that merely COERCES to the right integer ("1", 1.0, True). The
    token is the fence; a coerced value is not the fence the store minted.
  * the TOCTOU `check_writer` cannot close by itself: check at T, the lease
    expires, a successor acquires, and the old writer's effect lands at T+1.
    `lease_guard` holds `BEGIN IMMEDIATE` across the effect and re-reads the
    fence at the trusted store clock before the commit.

LEVEL: LOCAL / HERMETIC. A temp SQLite file, an injected clock, no daemon, no
network. The concurrency case uses two store instances over one temp database;
it proves serialization on this host, not a distributed guarantee.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest

from workengine.authority import AuthorityError, LeaseGrant, OwnerVerifiedLease
from workengine.store import LeaseError, ServerOSStore

RESOURCE = "work-write:SRV-0001"
A = "agent:device:tokyo/cc/A"
B = "agent:device:dell/cc/B"
PROJECT = "serveros"
ITEM = "SRV-0001"
ACTIONS = ("lease.acquire", "lease.renew", "lease.release", "work.commit")


class MutableClock:
    """A clock the test moves by hand. Never a production clock."""

    def __init__(self, value: float) -> None:
        self.value = float(value)

    def __call__(self) -> float:
        return self.value

    def set(self, value: float) -> None:
        self.value = float(value)


def a_grant(*, expires_at: float = 2000.0) -> LeaseGrant:
    return LeaseGrant(
        principal="human:A",
        grant_ref="grant:a",
        project=PROJECT,
        items=(ITEM,),
        actions=ACTIONS,
        valid_from=900.0,
        expires_at=expires_at,
        revoked=False,
        epoch=7,
        os_principal=f"uid:{os.getuid()}",
    )


class StubAuthority:
    """A test authority. It is not a production trust source."""

    def __init__(self, grant: LeaseGrant) -> None:
        self.grant = grant
        self.calls: list[tuple] = []

    def verify(self, context_ref, *, action, project, item_ref, now):
        self.calls.append((context_ref, action, project, item_ref, now))
        return self.grant


class LeaseHardeningTest(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.clock = MutableClock(1000.0)
        self.store = ServerOSStore(self._td.name, clock=self.clock)

    # -- helpers -----------------------------------------------------------

    def _acquire(self, *, owner: str = A, ttl_s=30.0, **kwargs) -> dict:
        return self.store.acquire_lease(RESOURCE, owner=owner, ttl_s=ttl_s,
                                        **kwargs)

    def _probe_count(self) -> int | None:
        with self.store.transaction() as conn:
            found = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='s1_probe'").fetchone()
            if found is None:
                return None
            return conn.execute("SELECT count(*) AS n FROM s1_probe").fetchone()["n"]

    def _assert_no_probe(self) -> None:
        self.assertIsNone(self._probe_count(),
                          "a refused effect must not leave its table behind")

    # -- TTL ---------------------------------------------------------------

    def test_a_non_positive_or_non_finite_ttl_is_refused(self) -> None:
        for bad in (0, -0.001, -30, float("nan"), float("inf"), float("-inf")):
            with self.subTest(ttl=bad):
                with self.assertRaises(LeaseError) as ctx:
                    self._acquire(ttl_s=bad)
                self.assertEqual(ctx.exception.reason_code, "LEASE_TTL_INVALID")
                self.assertIsNone(self.store.active_lease(RESOURCE),
                                  "a refused acquire must not write a lease")

    def test_a_non_numeric_ttl_is_refused(self) -> None:
        for bad in ("30", None, True, object()):
            with self.subTest(ttl=bad):
                with self.assertRaises(LeaseError) as ctx:
                    self._acquire(ttl_s=bad)
                self.assertEqual(ctx.exception.reason_code, "LEASE_TTL_INVALID")

    def test_an_unbounded_ttl_is_refused(self) -> None:
        with self.assertRaises(LeaseError) as ctx:
            self._acquire(ttl_s=7 * 24 * 3600 + 1)
        self.assertEqual(ctx.exception.reason_code, "LEASE_TTL_INVALID")

    def test_a_refused_ttl_leaves_the_live_lease_untouched(self) -> None:
        first = self._acquire()
        for bad in (0, float("nan"), 10 ** 12):
            with self.subTest(ttl=bad):
                with self.assertRaises(LeaseError):
                    self._acquire(owner=B, ttl_s=bad)
        live = self.store.active_lease(RESOURCE)
        self.assertEqual(live["owner"], A)
        self.assertEqual(live["token"], first["token"])

    # -- the clock ---------------------------------------------------------

    def test_a_non_finite_clock_cannot_take_over_a_live_lease(self) -> None:
        first = self._acquire()
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(now=bad):
                with self.assertRaises(LeaseError) as ctx:
                    self._acquire(owner=B, now=bad)
                self.assertEqual(ctx.exception.reason_code, "LEASE_CLOCK_INVALID")
        live = self.store.active_lease(RESOURCE)
        self.assertEqual(live["owner"], A)
        self.assertEqual(live["token"], first["token"])

    # -- malformed rows ----------------------------------------------------

    def test_a_malformed_lease_row_is_a_typed_refusal(self) -> None:
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO leases(resource,owner,token,expires_at) "
                "VALUES (?,?,?,?)", (RESOURCE, A, 1, "not-a-time"))
        with self.assertRaises(LeaseError) as ctx:
            self._acquire(owner=B)
        self.assertEqual(ctx.exception.reason_code, "LEASE_ROW_INVALID")
        with self.assertRaises(LeaseError) as ctx:
            self.store.active_lease(RESOURCE)
        self.assertEqual(ctx.exception.reason_code, "LEASE_ROW_INVALID")

    # -- identity / fence types -------------------------------------------

    def test_an_empty_owner_is_refused(self) -> None:
        for owner in ("", "   ", None):
            with self.subTest(owner=owner):
                with self.assertRaises(LeaseError) as ctx:
                    self._acquire(owner=owner)
                self.assertEqual(ctx.exception.reason_code, "LEASE_OWNER_REQUIRED")

    def test_a_coerced_token_is_not_the_fence(self) -> None:
        got = self._acquire()
        for bad in (True, "1", 1.0):
            with self.subTest(token=bad):
                with self.assertRaises(LeaseError) as ctx:
                    self.store.check_writer(RESOURCE, owner=A, token=bad, now=1000.0)
                self.assertEqual(ctx.exception.reason_code, "STALE_TOKEN")

    # -- the atomic commit boundary ---------------------------------------

    def test_lease_guard_refuses_an_expired_lease_before_the_effect(self) -> None:
        got = self._acquire()
        self.clock.set(1031.0)
        with self.assertRaises(LeaseError) as ctx:
            with self.store.lease_guard(RESOURCE, owner=A, token=got["token"]) as conn:
                conn.execute("CREATE TABLE s1_probe(x INTEGER)")
        self.assertEqual(ctx.exception.reason_code, "LEASE_EXPIRED")
        self._assert_no_probe()

    def test_lease_guard_rolls_back_an_effect_that_overran_its_ttl(self) -> None:
        got = self._acquire()
        with self.assertRaises(LeaseError) as ctx:
            with self.store.lease_guard(RESOURCE, owner=A, token=got["token"]) as conn:
                conn.execute("CREATE TABLE s1_probe(x INTEGER)")
                conn.execute("INSERT INTO s1_probe VALUES (1)")
                # The turn overran its TTL while the effect was running.
                self.clock.set(1031.0)
        self.assertEqual(ctx.exception.reason_code, "LEASE_EXPIRED")
        self._assert_no_probe()

    def test_a_check_before_a_takeover_is_not_an_authorization_to_write(self) -> None:
        got = self._acquire()
        self.assertTrue(self.store.check_writer(
            RESOURCE, owner=A, token=got["token"], now=1010.0)["authorized"])

        other = ServerOSStore(self._td.name, clock=MutableClock(1031.0))
        second = other.acquire_lease(RESOURCE, owner=B, ttl_s=30.0)
        self.assertGreater(second["token"], got["token"])

        self.clock.set(1031.0)
        with self.assertRaises(LeaseError) as ctx:
            with self.store.lease_guard(RESOURCE, owner=A, token=got["token"]) as conn:
                conn.execute("CREATE TABLE s1_probe(x INTEGER)")
        self.assertEqual(ctx.exception.reason_code, "STALE_TOKEN")
        self._assert_no_probe()

    def test_lease_guard_serializes_a_takeover_until_the_effect_commits(self) -> None:
        got = self._acquire()
        other = ServerOSStore(self._td.name, clock=MutableClock(1031.0))
        outcome: dict = {}

        def contender() -> None:
            try:
                outcome["lease"] = other.acquire_lease(
                    RESOURCE, owner=B, ttl_s=30.0)
            except BaseException as exc:  # reported, never swallowed
                outcome["error"] = exc

        with self.store.lease_guard(RESOURCE, owner=A, token=got["token"]) as conn:
            conn.execute("CREATE TABLE s1_probe(x INTEGER)")
            conn.execute("INSERT INTO s1_probe VALUES (1)")
            thread = threading.Thread(target=contender, daemon=True)
            thread.start()
            time.sleep(0.3)
            self.assertTrue(
                thread.is_alive(),
                "the successor acquired while the effect held the write lock")
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "the successor did not finish")
        self.assertNotIn("error", outcome, outcome.get("error"))
        self.assertGreater(outcome["lease"]["token"], got["token"])
        self.assertEqual(self._probe_count(), 1,
                         "the effect that held the fence must commit")

    # -- the adapter's commit boundary ------------------------------------

    def test_commit_guard_rechecks_the_grant_after_the_effect(self) -> None:
        lease = OwnerVerifiedLease(self.store, StubAuthority(a_grant(expires_at=1005.0)))
        got = lease.acquire(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                            ttl_s=30.0)
        with self.assertRaises(AuthorityError) as ctx:
            with lease.commit_guard(context_ref="ctx:1", project=PROJECT,
                                    item_ref=ITEM, token=got["token"]) as conn:
                conn.execute("CREATE TABLE s1_probe(x INTEGER)")
                conn.execute("INSERT INTO s1_probe VALUES (1)")
                # The grant lapses while the effect is running; the lease (expires
                # at 1030) is still live, so only the second grant read catches it.
                self.clock.set(1010.0)
        self.assertEqual(ctx.exception.reason_code, "GRANT_EXPIRED")
        self._assert_no_probe()

    def test_commit_guard_commits_the_effect_under_the_fence(self) -> None:
        lease = OwnerVerifiedLease(self.store, StubAuthority(a_grant()))
        got = lease.acquire(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                            ttl_s=30.0)
        with lease.commit_guard(context_ref="ctx:1", project=PROJECT,
                                item_ref=ITEM, token=got["token"]) as conn:
            conn.execute("CREATE TABLE s1_probe(x INTEGER)")
            conn.execute("INSERT INTO s1_probe VALUES (1)")
        self.assertEqual(self._probe_count(), 1)


if __name__ == "__main__":
    unittest.main()
