"""The exclusive Work write lease, and the fence that stops a stale holder.

WHY THIS FILE EXISTS
--------------------
`schema.sql` has declared a `leases(resource, owner, token, expires_at)` table
since the first migration, and until now nothing in this package read or wrote a
row of it. That is the shape this repository keeps finding: a table that reads
as a mutual-exclusion mechanism because it is declared, and no code that makes it
one. Issue #237 (B3 / T6) names it directly.

Three facts are separated here, and they are separated because three different
sentences go wrong when one stands in for another:

  * A row EXISTING is not a lease being HELD. `expires_at` in the past is a
    crash, not a lock. T6.
  * A lease HELD is not a lease the caller still OWNS. A holder that was
    superseded cannot commit merely because its process is still running — the
    token is what moved, not the process. T2.
  * A lease held at the WRONG EPOCH authorizes nothing. An authorization
    checked once before a long turn is not an authorization at commit time. T3.

`runtime/lease_store.py` one repository over is a DIFFERENT thing — it records
resource occupancy (who WANTS a resource), and its own module docstring says so.
This table is exclusive write OWNERSHIP of a Work scope. The test below uses the
word "lease" for this one only.

Hermetic: a temp SQLite file, no clock, no machine.
"""
from __future__ import annotations

import tempfile
import unittest

from workengine.store import LeaseError, ServerOSStore

RESOURCE = "work-write:SRV-0001"
A = "agent:device:tokyo/cc/A"
B = "agent:device:dell/cc/B"


class LeaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.store = ServerOSStore(self._td.name)

    # -- T6: an expired lease is not held ----------------------------------

    def test_held_leases_excludes_an_expired_lease(self):
        """T6 — a lease expired in the DB is not active ownership.

        The bug #237 records is a reader that does `SELECT resource FROM leases`
        and treats every row as a live claim. An expired row is a crashed
        holder, and reporting it as held is reporting a lock nobody holds.
        """
        self.store.acquire_lease(RESOURCE, owner=A, ttl_s=30, now=1000.0)

        self.assertEqual(self.store.held_leases(now=1010.0), [RESOURCE],
                         "a lease inside its TTL is held")
        self.assertEqual(self.store.held_leases(now=1031.0), [],
                         "a lease past its TTL is not held")
        # The row is still there — it is the READING that must exclude it, not
        # a sweeping delete that would erase the evidence of the crash.
        self.assertIsNone(self.store.active_lease(RESOURCE, now=1031.0))

    def test_an_expired_row_stays_in_the_table_as_evidence(self):
        """Excluding an expired row from the reader is not deleting the row.

        A crashed holder leaves the only evidence that it ever held the scope.
        A sweep that removed it would make the crash indistinguishable from a
        scope nobody ever claimed — the evidence T6 asks to keep answerable.
        """
        self.store.acquire_lease(RESOURCE, owner=A, ttl_s=30, now=1000.0)
        self.assertIsNone(self.store.active_lease(RESOURCE, now=1031.0))
        with self.store.transaction() as conn:
            rows = conn.execute("SELECT resource FROM leases").fetchall()
        self.assertEqual([row["resource"] for row in rows], [RESOURCE])

    def test_active_lease_reports_the_owner_and_token(self):
        got = self.store.acquire_lease(RESOURCE, owner=A, ttl_s=30, now=1000.0)
        live = self.store.active_lease(RESOURCE, now=1001.0)
        self.assertEqual(live["owner"], A)
        self.assertEqual(live["token"], got["token"])

    # -- T2: two holders cannot hold one write scope ------------------------

    def test_two_holders_cannot_both_hold_the_same_scope(self):
        """T2 — at most one authoritative writer for a scope.

        B's acquire while A's lease is live is refused, and the refusal names the
        holder B is up against. The loser is fenced; it is not a race that
        whoever writes last wins.
        """
        self.store.acquire_lease(RESOURCE, owner=A, ttl_s=30, now=1000.0)
        with self.assertRaises(LeaseError) as ctx:
            self.store.acquire_lease(RESOURCE, owner=B, ttl_s=30, now=1001.0)
        self.assertEqual(ctx.exception.reason_code, "LEASE_HELD_BY_OTHER")
        # And B still does not own it.
        self.assertEqual(self.store.active_lease(RESOURCE, now=1001.0)["owner"], A)

    def test_a_successor_gets_a_higher_token_after_expiry(self):
        """The token is monotonic, so 'newer' is decidable after the fact.

        A successor that could reuse a number the predecessor already held would
        make the fence unable to tell them apart — which is the whole reason a
        token exists rather than a boolean.
        """
        first = self.store.acquire_lease(RESOURCE, owner=A, ttl_s=30, now=1000.0)
        second = self.store.acquire_lease(RESOURCE, owner=B, ttl_s=30, now=1031.0)
        self.assertGreater(second["token"], first["token"])

    def test_the_token_is_monotonic_across_a_store_restart(self):
        """The fence is durable, not process memory.

        The successor is a NEW store over the same database — the shape of a
        second process starting after the first one crashed. It must still mint
        a higher token, and the first process's token must still be fenced.
        """
        first = self.store.acquire_lease(RESOURCE, owner=A, ttl_s=30, now=1000.0)
        reopened = ServerOSStore(self._td.name)
        second = reopened.acquire_lease(RESOURCE, owner=B, ttl_s=30, now=1031.0)
        self.assertGreater(second["token"], first["token"])
        with self.assertRaises(LeaseError) as ctx:
            reopened.check_writer(RESOURCE, owner=A, token=first["token"], now=1032.0)
        self.assertEqual(ctx.exception.reason_code, "STALE_TOKEN")

    # -- T3: an expired lease authorizes nothing ----------------------------

    def test_an_expired_lease_cannot_authorize_a_commit(self):
        """T3 — checked at the commit boundary, not only at the door.

        A turn that acquired a lease and then ran past its TTL must not commit
        on the strength of the acquisition. `check_writer` is that boundary.
        """
        got = self.store.acquire_lease(RESOURCE, owner=A, ttl_s=30, now=1000.0)
        # Inside the TTL: the holder may commit.
        ok = self.store.check_writer(RESOURCE, owner=A, token=got["token"], now=1010.0)
        self.assertTrue(ok["authorized"])
        # Past the TTL: the same holder with the same token is fenced.
        with self.assertRaises(LeaseError) as ctx:
            self.store.check_writer(RESOURCE, owner=A, token=got["token"], now=1031.0)
        self.assertEqual(ctx.exception.reason_code, "LEASE_EXPIRED")

    # -- fencing: an old token after promotion is rejected ------------------

    def test_an_old_token_is_rejected_after_promotion(self):
        """The stale writer's process is alive AND its token is dead.

        This is the case the doc calls out: 'old token after promotion is
        rejected even if its process is still alive.' Nothing here kills A; A is
        simply no longer the holder, and its token says so.
        """
        first = self.store.acquire_lease(RESOURCE, owner=A, ttl_s=30, now=1000.0)
        # A's lease lapses and B is promoted.
        second = self.store.acquire_lease(RESOURCE, owner=B, ttl_s=30, now=1031.0)
        self.assertGreater(second["token"], first["token"])

        with self.assertRaises(LeaseError) as ctx:
            self.store.check_writer(RESOURCE, owner=A, token=first["token"], now=1032.0)
        self.assertEqual(ctx.exception.reason_code, "STALE_TOKEN")
        # B, the current holder, is authorized.
        self.assertTrue(
            self.store.check_writer(RESOURCE, owner=B, token=second["token"], now=1032.0)["authorized"])

    def test_a_writer_holding_no_lease_is_rejected(self):
        self.store.acquire_lease(RESOURCE, owner=A, ttl_s=30, now=1000.0)
        with self.assertRaises(LeaseError) as ctx:
            self.store.check_writer(RESOURCE, owner=B, token=999, now=1000.0)
        self.assertIn(ctx.exception.reason_code, ("NOT_HOLDER", "STALE_TOKEN"))

    # -- renewal and release are holder-only --------------------------------

    def test_only_the_current_holder_may_renew(self):
        got = self.store.acquire_lease(RESOURCE, owner=A, ttl_s=30, now=1000.0)
        renewed = self.store.renew_lease(RESOURCE, owner=A, token=got["token"],
                                         ttl_s=60, now=1020.0)
        self.assertEqual(renewed["token"], got["token"])
        self.assertEqual(self.store.active_lease(RESOURCE, now=1070.0)["owner"], A)
        with self.assertRaises(LeaseError):
            self.store.renew_lease(RESOURCE, owner=B, token=got["token"],
                                   ttl_s=60, now=1021.0)

    def test_release_requires_the_current_token(self):
        got = self.store.acquire_lease(RESOURCE, owner=A, ttl_s=30, now=1000.0)
        with self.assertRaises(LeaseError):
            self.store.release_lease(RESOURCE, owner=A, token=got["token"] + 1, now=1001.0)
        self.store.release_lease(RESOURCE, owner=A, token=got["token"], now=1001.0)
        self.assertIsNone(self.store.active_lease(RESOURCE, now=1001.0))
        # A released scope can be acquired again.
        self.store.acquire_lease(RESOURCE, owner=B, ttl_s=30, now=1002.0)

    def test_acquire_of_a_free_scope_is_immediate(self):
        got = self.store.acquire_lease(RESOURCE, owner=A, ttl_s=30, now=1000.0)
        self.assertEqual(got["resource"], RESOURCE)
        self.assertEqual(got["owner"], A)
        self.assertIsInstance(got["token"], int)


if __name__ == "__main__":
    unittest.main()
