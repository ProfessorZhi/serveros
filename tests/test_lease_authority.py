"""The lease producer is a trust seam, not a caller-asserted owner.

WHY THIS FILE EXISTS
--------------------
Independent QA #256 Snapshot H found that `ServerOSStore.acquire_lease` takes
`owner=` from its caller. That is correct for a store primitive and wrong for a
producer: any caller can write any owner into `leases`, so serveros
#261/#262/#264 fail closed for want of an authenticated producer.

This repository has no OS-principal+grant verified CLI or MCP action. The MCP
surface can resolve a `context_ref` when a resolver is injected, but the default
is `None` and the surface exposes no lease verb; the CLI exposes none either.
The safe producer is therefore a seam that refuses with `BLOCKED_TRUST_SOURCE`
until a trusted parent is bound. It never falls back to the caller's word.

The tests below are the refusal matrix. The one positive path uses a test
authority to prove the seam can carry a real grant; it is not a production
authority and the module is deliberately absent from the CLI and MCP.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from workengine.authority import (
    AuthorityError,
    LeaseGrant,
    OwnerVerifiedLease,
)
from workengine.store import ServerOSStore

PROJECT = "serveros"
ITEM = "SRV-0001"
RESOURCE = "work-write:SRV-0001"
PRINCIPAL = "human:hwz"
OTHER = "human:other"
GRANT = "grant:serveros:w1"
EPOCH = 7
ACTIONS = ("lease.acquire", "lease.renew", "lease.release", "work.commit")


def grant(**overrides) -> LeaseGrant:
    values = dict(
        principal=PRINCIPAL,
        grant_ref=GRANT,
        project=PROJECT,
        items=(ITEM,),
        actions=ACTIONS,
        valid_from=900.0,
        expires_at=2000.0,
        revoked=False,
        epoch=EPOCH,
        os_principal=f"uid:{os.getuid()}",
    )
    values.update(overrides)
    return LeaseGrant(**values)


class StubAuthority:
    """A test authority. It is not a production trust source."""

    def __init__(self, result=None):
        self.result = result or grant()
        self.error = None
        self.calls = []

    def verify(self, context_ref, *, action, project, item_ref, now):
        self.calls.append((context_ref, action, project, item_ref, now))
        if self.error is not None:
            raise self.error
        return self.result


class LeaseAuthorityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.store = ServerOSStore(self._td.name)

    def _lease(self, authority=None) -> OwnerVerifiedLease:
        return OwnerVerifiedLease(self.store, authority or StubAuthority())

    def _refused(self, lease, code, **overrides):
        with self.assertRaises(AuthorityError) as ctx:
            lease.acquire(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                          ttl_s=30.0, now=1000.0, **overrides)
        self.assertEqual(ctx.exception.reason_code, code, str(ctx.exception))
        self.assertIsNone(self.store.active_lease(RESOURCE, now=1000.0),
                          "a refused acquire must not write a lease")
        return ctx.exception

    # -- no trusted source: fail closed ------------------------------------

    def test_no_authority_is_bound_and_acquire_is_blocked_trust_source(self):
        self._refused(OwnerVerifiedLease(self.store), "BLOCKED_TRUST_SOURCE")

    def test_the_adapter_does_not_accept_a_caller_supplied_owner(self):
        lease = self._lease()
        with self.assertRaises(TypeError):
            lease.acquire(context_ref="ctx:1", owner=PRINCIPAL, project=PROJECT,
                          item_ref=ITEM, ttl_s=30.0, now=1000.0)

    def test_an_empty_context_ref_is_untrusted_actor(self):
        authority = StubAuthority()
        lease = OwnerVerifiedLease(self.store, authority)
        with self.assertRaises(AuthorityError) as ctx:
            lease.acquire(context_ref="", project=PROJECT, item_ref=ITEM,
                          ttl_s=30.0, now=1000.0)
        self.assertEqual(ctx.exception.reason_code, "UNTRUSTED_ACTOR")
        self.assertEqual(authority.calls, [], "nothing may be resolved from an empty ref")

    def test_a_verifier_that_cannot_identify_the_caller_is_refused(self):
        self._refused(self._lease(StubAuthority(grant(principal=""))), "UNTRUSTED_ACTOR")

    def test_a_verifier_that_raises_is_refused(self):
        authority = StubAuthority()
        authority.error = RuntimeError("resolver unavailable")
        self._refused(self._lease(authority), "UNTRUSTED_ACTOR")

    # -- exact Item / project / action / grant binding ----------------------

    def test_cross_project_is_refused(self):
        self._refused(self._lease(StubAuthority(grant(project="other"))), "PROJECT_MISMATCH")

    def test_item_not_in_the_grant_is_refused(self):
        self._refused(self._lease(StubAuthority(grant(items=("SRV-9999",)))), "ITEM_MISMATCH")

    def test_action_not_in_the_grant_is_refused(self):
        self._refused(self._lease(StubAuthority(grant(actions=("lease.renew",)))),
                      "ACTION_NOT_GRANTED")

    def test_revoked_grant_is_refused(self):
        self._refused(self._lease(StubAuthority(grant(revoked=True))), "GRANT_REVOKED")

    def test_expired_grant_is_refused(self):
        self._refused(self._lease(StubAuthority(grant(expires_at=1000.0))), "GRANT_EXPIRED")

    def test_not_yet_valid_grant_is_refused(self):
        self._refused(self._lease(StubAuthority(grant(valid_from=1001.0))),
                      "GRANT_NOT_YET_VALID")

    def test_stale_epoch_is_refused(self):
        self._refused(self._lease(StubAuthority(grant(epoch=7))), "EPOCH_STALE",
                      expected_epoch=6)

    def test_os_principal_must_match_this_process(self):
        self._refused(self._lease(StubAuthority(grant(os_principal="uid:99999"))),
                      "OS_PRINCIPAL_MISMATCH")

    def test_an_epoch_less_grant_is_refused(self):
        """An epoch-less grant cannot be fenced, so it is not a grant."""
        self._refused(self._lease(StubAuthority(grant(epoch=0))), "EPOCH_MISSING")

    def test_a_grant_without_a_ref_is_untrusted_actor(self):
        self._refused(self._lease(StubAuthority(grant(grant_ref=""))),
                      "UNTRUSTED_ACTOR")

    def test_an_authority_that_returns_nothing_is_refused(self):
        class Nothing:
            def verify(self, context_ref, *, action, project, item_ref, now):
                return None

        self._refused(OwnerVerifiedLease(self.store, Nothing()), "UNTRUSTED_ACTOR")

    def test_the_resource_cannot_be_supplied_by_the_caller(self):
        """The scope is derived from the verified Item, not passed in."""
        lease = self._lease()
        with self.assertRaises(TypeError):
            lease.acquire(context_ref="ctx:1", resource="work-write:OTHER",
                          project=PROJECT, item_ref=ITEM, ttl_s=30.0, now=1000.0)

    # -- the one positive path, with a test authority -----------------------

    def test_authorized_original_actor_can_acquire(self):
        result = self._lease().acquire(context_ref="ctx:1", project=PROJECT,
                                       item_ref=ITEM, ttl_s=30.0, now=1000.0)
        self.assertEqual(result["owner"], PRINCIPAL)
        self.assertEqual(result["resource"], RESOURCE)
        self.assertIsInstance(result["token"], int)
        live = self.store.active_lease(RESOURCE, now=1000.0)
        self.assertEqual(live["owner"], PRINCIPAL)

    def test_second_actor_on_the_same_item_is_refused(self):
        self._lease().acquire(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                              ttl_s=30.0, now=1000.0)
        with self.assertRaises(AuthorityError) as ctx:
            self._lease(StubAuthority(grant(principal=OTHER, grant_ref="grant:other"))) \
                .acquire(context_ref="ctx:2", project=PROJECT, item_ref=ITEM,
                         ttl_s=30.0, now=1001.0)
        self.assertEqual(ctx.exception.reason_code, "LEASE_HELD_BY_OTHER")
        self.assertEqual(self.store.active_lease(RESOURCE, now=1001.0)["owner"], PRINCIPAL)

    def test_duplicate_acquire_is_refused_not_a_silent_reacquire(self):
        lease = self._lease()
        first = lease.acquire(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                              ttl_s=30.0, now=1000.0)
        with self.assertRaises(AuthorityError) as ctx:
            lease.acquire(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                          ttl_s=30.0, now=1001.0)
        self.assertEqual(ctx.exception.reason_code, "LEASE_HELD_BY_OTHER")
        self.assertEqual(self.store.active_lease(RESOURCE, now=1001.0)["token"],
                         first["token"], "a duplicate acquire must not move the fence")

    def test_a_successor_after_expiry_gets_a_higher_token(self):
        first = self._lease().acquire(context_ref="ctx:1", project=PROJECT,
                                      item_ref=ITEM, ttl_s=30.0, now=1000.0)
        second = self._lease(StubAuthority(grant(principal=OTHER, grant_ref="grant:other"))) \
            .acquire(context_ref="ctx:2", project=PROJECT, item_ref=ITEM,
                     ttl_s=30.0, now=1031.0)
        self.assertGreater(second["token"], first["token"])

    # -- renewal, release and the commit boundary ---------------------------

    def test_renew_requires_the_current_token_and_a_live_grant(self):
        lease = self._lease()
        got = lease.acquire(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                            ttl_s=30.0, now=1000.0)
        with self.assertRaises(AuthorityError) as ctx:
            lease.renew(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                        token=got["token"] + 1, ttl_s=60.0, now=1010.0)
        self.assertEqual(ctx.exception.reason_code, "STALE_TOKEN")

        renewed = lease.renew(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                              token=got["token"], ttl_s=60.0, now=1010.0)
        self.assertEqual(renewed["token"], got["token"])

        expired_grant = OwnerVerifiedLease(
            self.store, StubAuthority(grant(expires_at=1000.0)))
        with self.assertRaises(AuthorityError) as ctx:
            expired_grant.renew(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                                token=got["token"], ttl_s=60.0, now=1070.0)
        self.assertEqual(ctx.exception.reason_code, "GRANT_EXPIRED")

    def test_release_requires_the_current_token(self):
        lease = self._lease()
        got = lease.acquire(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                            ttl_s=30.0, now=1000.0)
        with self.assertRaises(AuthorityError) as ctx:
            lease.release(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                          token=got["token"] + 1, now=1001.0)
        self.assertEqual(ctx.exception.reason_code, "STALE_TOKEN")
        released = lease.release(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                                 token=got["token"], now=1001.0)
        self.assertTrue(released["released"])
        self.assertIsNone(self.store.active_lease(RESOURCE, now=1001.0))

    def test_release_after_grant_revocation_is_refused(self):
        got = self._lease().acquire(context_ref="ctx:1", project=PROJECT,
                                    item_ref=ITEM, ttl_s=30.0, now=1000.0)
        revoked = OwnerVerifiedLease(self.store, StubAuthority(grant(revoked=True)))
        with self.assertRaises(AuthorityError) as ctx:
            revoked.release(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                            token=got["token"], now=1001.0)
        self.assertEqual(ctx.exception.reason_code, "GRANT_REVOKED")
        self.assertEqual(self.store.active_lease(RESOURCE, now=1001.0)["owner"], PRINCIPAL)

    def test_commit_boundary_requires_a_current_grant_and_token(self):
        lease = self._lease()
        got = lease.acquire(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                            ttl_s=30.0, now=1000.0)
        self.assertTrue(lease.check_writer(context_ref="ctx:1", project=PROJECT,
                                           item_ref=ITEM, token=got["token"],
                                           now=1010.0)["authorized"])
        with self.assertRaises(AuthorityError) as ctx:
            lease.check_writer(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                               token=got["token"] + 1, now=1010.0)
        self.assertEqual(ctx.exception.reason_code, "STALE_TOKEN")
        with self.assertRaises(AuthorityError) as ctx:
            lease.check_writer(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                               token=got["token"], now=1031.0)
        self.assertEqual(ctx.exception.reason_code, "LEASE_EXPIRED")
        with self.assertRaises(AuthorityError) as ctx:
            OwnerVerifiedLease(self.store, StubAuthority(grant(revoked=True))) \
                .check_writer(context_ref="ctx:1", project=PROJECT, item_ref=ITEM,
                              token=got["token"], now=1010.0)
        self.assertEqual(ctx.exception.reason_code, "GRANT_REVOKED")

    def test_a_grant_that_lapses_before_commit_refuses_the_commit(self):
        """The lease token is still current; only the grant lapsed (T3).

        This is why the adapter re-verifies at the commit boundary instead of
        trusting the acquisition: the grant and the lease are two clocks.
        """
        got = self._lease().acquire(context_ref="ctx:1", project=PROJECT,
                                    item_ref=ITEM, ttl_s=30.0, now=1000.0)
        lapsed = OwnerVerifiedLease(
            self.store, StubAuthority(grant(expires_at=1005.0)))
        with self.assertRaises(AuthorityError) as ctx:
            lapsed.check_writer(context_ref="ctx:1", project=PROJECT,
                                item_ref=ITEM, token=got["token"], now=1010.0)
        self.assertEqual(ctx.exception.reason_code, "GRANT_EXPIRED")
        # The lease itself is still live; the refusal came from the grant.
        self.assertIsNotNone(self.store.active_lease(RESOURCE, now=1010.0))

    # -- the unguarded producer must not be exposed -------------------------

    def test_no_lease_verb_is_exposed_on_the_cli(self):
        from workengine.cli import build_parser
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["lease", "acquire"])

    def test_no_lease_tool_is_exposed_on_the_mcp_surface(self):
        from workengine.mcp import tool_names
        self.assertFalse([name for name in tool_names() if "lease" in name])


if __name__ == "__main__":
    unittest.main()
