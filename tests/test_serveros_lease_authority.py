"""The cross-repo trusted parent: serverosd #272 -> Work Engine LeaseGrant.

WHY THIS FILE EXISTS
--------------------
Work Engine #14's `OwnerVerifiedLease` refuses without a `LeaseAuthority`, and
serveros #272 added exactly the read it needs: `agent.lease.authority.verify`
resolves the context in-process, reads the kernel `SO_PEERCRED` peer, and
re-checks the live grant's `delegated_by`, project, Item, actions, revocation,
window and epoch. It is a READ; it mints no grant and acquires no lease.

This test is the one real cross-repo connection. It materializes the ACTUAL
serveros #272 source (`fc93590...`, the PR #272 tip) out of THIS repository's
object store into a temp tree, builds a hermetic daemon with a temp state dir
and a temp AF_UNIX socket, and points the Work Engine adapter at it. The peer is
the kernel's answer for the connecting test process; the context record and
grant are test-minted in the temp store.

EVIDENCE LEVEL: LOCAL / HERMETIC / REAL_SO_PEERCRED / SYNTHETIC_CONTEXT_AND_GRANT.
The synthetic peer cases (foreign uid, missing peer) use the daemon's
`handle(peer=...)` seam and are labelled as such. This is not REAL_DEVICE and
not a production daemon.

#272 is an OPEN pull request, not yet on `main`. When its object is absent from
this repository the class skips with a precise reason; CI without that fetch is
not silently counted as a pass. `SERVEROS_LEASE_AUTHORITY_REF` may pin another
fetched ref for a future revision of the read.
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

from workengine import WorkItem
from workengine.authority import AuthorityError, OwnerVerifiedLease
from workengine.serveros_authority import (
    ServerOSLeaseAuthority,
    UnixSocketVerifyClient,
)
from workengine.store import ServerOSStore

REPO = Path(__file__).resolve().parents[1]
# The verified read lives on PR #272, not on main. Materialize that exact
# source from this repository's object store; no machine-specific sibling
# checkout and no hardcoded environment path is involved.
SERVEROS_REF = os.environ.get(
    "SERVEROS_LEASE_AUTHORITY_REF",
    "fc93590030c218934252920a53aa67277f144844")
PROJECT = "serveros"
ITEM = "SRV-0001"
RESOURCE = "work-write:SRV-0001"
ACTIONS = ("lease.acquire", "lease.renew", "lease.release", "work.commit")

_MODULES: dict[str, object] = {}


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _HandleClient:
    """The daemon's own `handle(peer=...)` seam. Used for synthetic peers."""

    def __init__(self, daemon, peer):
        self.daemon = daemon
        self.peer = peer

    def verify(self, context_ref, *, grant_ref, action, project, item_ref):
        answer = self.daemon.handle({
            "method": "agent.lease.authority.verify",
            "params": {
                "context_ref": context_ref, "grant_ref": grant_ref,
                "action": action, "project": project, "item_ref": item_ref,
            },
        }, peer=self.peer)
        if not answer.get("ok"):
            error = answer.get("error") or {}
            raise AuthorityError(error.get("code") or "UNTRUSTED_ACTOR",
                                 error.get("message") or "daemon refused")
        return answer["result"]


class _NoGrantClient:
    """A test double that claims success without returning a grant."""

    def verify(self, context_ref, *, grant_ref, action, project, item_ref):
        return {"verified": True}


class _WrongGrantClient:
    """A test double whose grant is not the bound grant."""

    def verify(self, context_ref, *, grant_ref, action, project, item_ref):
        return {"verified": True, "grant": {
            "grant_ref": "grant:somebody-else", "principal": "human:X",
            "os_principal": f"uid:{os.getuid()}", "epoch": 1,
            "valid_from": "2000-01-01T00:00:00+00:00",
            "expires_at": "2999-01-01T00:00:00+00:00", "revoked": False,
        }}


class _AnswerClient:
    """A test double returning one canned verify answer."""

    def __init__(self, answer):
        self.answer = answer

    def verify(self, context_ref, *, grant_ref, action, project, item_ref):
        return self.answer


def _verified_grant(**overrides) -> dict:
    grant = {
        "grant_ref": "grant:a", "principal": "human:A",
        "os_principal": f"uid:{os.getuid()}", "epoch": 1,
        "valid_from": "2000-01-01T00:00:00+00:00",
        "expires_at": "2999-01-01T00:00:00+00:00", "revoked": False,
    }
    grant.update(overrides)
    return {"verified": True, "grant": grant}


class ServerOSLeaseAuthorityTest(unittest.TestCase):
    #: Module names the archived daemon loads by path. Other test modules in
    #: this suite cache the CURRENT checkout under these same names, so a plain
    #: `_load` would hand back the current daemon — which has no #272 verify
    #: read. Snapshot and clear them for the class, then restore them.
    _ARCHIVE_MODULE_PREFIXES = (
        "serveros_runtime_", "_serveros_", "serveros_context_")

    @classmethod
    def setUpClass(cls) -> None:
        probe = subprocess.run(
            ["git", "-C", str(REPO), "cat-file", "-e",
             f"{SERVEROS_REF}^{{commit}}"], capture_output=True)
        if probe.returncode != 0:
            raise unittest.SkipTest(
                f"serveros #272 source {SERVEROS_REF} is not present in this "
                f"repository; fetch the #272 branch (or set "
                f"SERVEROS_LEASE_AUTHORITY_REF) before running this proof")
        cls._saved_modules = {
            name: module for name, module in list(sys.modules.items())
            if name.startswith(cls._ARCHIVE_MODULE_PREFIXES)}
        for name in cls._saved_modules:
            del sys.modules[name]
        cls._tree = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tree.name)
        archive = cls.root / "tree.tar"
        with open(archive, "wb") as handle:
            subprocess.run(
                ["git", "-C", str(REPO), "archive", SERVEROS_REF],
                check=True, stdout=handle)
        subprocess.run(["tar", "-xf", str(archive), "-C", str(cls.root)],
                       check=True)
        sys.path.insert(0, str(cls.root))
        cls.daemon_mod = _load(
            "serveros_runtime_daemon", cls.root / "runtime" / "daemon.py")
        cls.registry_mod = sys.modules["serveros_runtime_registry"]
        cls.actor_mod = sys.modules["_serveros_context_actor"]
        cls.resolver_mod = _load(
            "serveros_context_resolver", cls.root / "context" / "resolver.py")

    @classmethod
    def tearDownClass(cls) -> None:
        # Drop the archived modules and put the pre-existing ones back, so a
        # later test module never sees the #272 tree through its own cache.
        for name in list(sys.modules):
            if (name.startswith(cls._ARCHIVE_MODULE_PREFIXES)
                    and name not in cls._saved_modules):
                del sys.modules[name]
        sys.modules.update(cls._saved_modules)
        try:
            sys.path.remove(str(cls.root))
        except ValueError:
            pass
        if hasattr(cls, "_tree"):
            cls._tree.cleanup()

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        (self.root / "run").mkdir(parents=True, exist_ok=True)
        self.sockpath = self.root / "run" / "serveros.sock"
        registry = self.registry_mod.Registry(document={
            "schema": "serveros/managed-runtime-registry.v1",
            "device_ref": "device:test",
            "resources": [],
        })
        self.daemon = self.daemon_mod.Daemon(
            registry, state_dir=self.root / "state",
            socket_path=self.sockpath, log=lambda message: None)
        self.we = ServerOSStore(self.root / "we")
        self.we.register_project(PROJECT, "SRV")
        self.we.create_item(WorkItem(ITEM, PROJECT, "x", "test", "/card"))
        self.ctx_a = self._context("human:A")
        self.grant_a = self._grant(self.ctx_a, "grant:a", ACTIONS)

    # -- fixture -----------------------------------------------------------

    def _iso(self, offset: float = 0.0) -> str:
        return self.daemon_mod._iso(self.daemon._now() + offset)

    def _context(self, actor: str) -> dict:
        now = self._iso()
        record = self.actor_mod.build({
            "human_control_provider": "provider:test",
            "transport": "feishu",
            "transport_identity": "test",
            "conversation_ref": "oc_cross_repo",
            "message_ref": "om_cross_repo",
            "observed_at": now,
            "provider_status": "VERIFIED",
            "provenance": "FACT",
            "actor_ref": actor,
            "provider_pid": os.getpid(),
        }, now=now)
        self.daemon.control_store().put_context(record)
        return record

    def _grant(self, context: dict, ref: str, actions=ACTIONS, *,
               projects=(PROJECT,), items=(ITEM,), valid_from_offset=-60,
               expires_in=600) -> dict:
        return self.daemon.control_store().put_grant({
            "grant_ref": ref,
            "delegated_by": context["context_ref"],
            "valid_from": self._iso(valid_from_offset),
            "expires_at": self._iso(expires_in),
            "projects": list(projects),
            "items": list(items),
            "actions": list(actions),
        })

    def _legacy_grant(self, context: dict, ref: str) -> dict:
        """A grant written before `epoch` existed: UNKNOWN, never backfilled."""
        store = self.daemon.control_store()
        record = {
            "grant_ref": ref,
            "delegated_by": context["context_ref"],
            "valid_from": self._iso(-60),
            "expires_at": self._iso(600),
            "projects": [PROJECT],
            "items": [ITEM],
            "actions": list(ACTIONS),
            "revoked": False,
        }
        with store._mutation():
            store._data["grants"][ref] = record
            store._flush()
        return record

    def _lease(self, *, grant_ref="grant:a", client=None) -> OwnerVerifiedLease:
        client = client or UnixSocketVerifyClient(self.sockpath)
        return OwnerVerifiedLease(
            self.we, ServerOSLeaseAuthority(client, grant_ref=grant_ref))

    @contextmanager
    def _socket(self):
        """One real AF_UNIX connection served by the actual #272 daemon."""
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.settimeout(5)
        server.bind(str(self.sockpath))
        server.listen(1)

        def serve_once():
            try:
                connection, _ = server.accept()
            except OSError:
                return
            try:
                self.daemon._serve_connection(connection)
            finally:
                connection.close()

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        try:
            yield self.sockpath
        finally:
            thread.join(timeout=5)
            server.close()
            try:
                self.sockpath.unlink()
            except OSError:
                pass

    def _assert_no_lease(self):
        self.assertIsNone(self.we.active_lease(RESOURCE, now=time.time()))

    def _acquire_refused(self, code, *, grant_ref="grant:a", client=None,
                         **overrides):
        with self._socket():
            with self.assertRaises(AuthorityError) as caught:
                self._lease(grant_ref=grant_ref, client=client).acquire(
                    context_ref=overrides.pop("context_ref", self.ctx_a["context_ref"]),
                    project=overrides.pop("project", PROJECT),
                    item_ref=overrides.pop("item_ref", ITEM),
                    ttl_s=30.0, now=time.time(), **overrides)
        self.assertEqual(caught.exception.reason_code, code,
                         str(caught.exception))
        self._assert_no_lease()
        return caught.exception

    # -- the real socket positive -----------------------------------------

    def test_real_socket_positive_and_canonical_resource(self):
        with self._socket():
            got = self._lease().acquire(
                context_ref=self.ctx_a["context_ref"], project=PROJECT,
                item_ref=ITEM, ttl_s=30.0, now=time.time())
        self.assertEqual(got["owner"], "human:A")
        self.assertEqual(got["resource"], RESOURCE)
        self.assertIsInstance(got["token"], int)
        live = self.we.active_lease(RESOURCE, now=time.time())
        self.assertEqual(live["owner"], "human:A")
        self.assertEqual(self.resolver_mod.work_write_resource(ITEM), RESOURCE)
        self.assertEqual(OwnerVerifiedLease._resource(ITEM), RESOURCE)

    # -- identity / peer refusals -----------------------------------------

    def test_a_grant_delegated_by_another_actor_is_refused(self):
        ctx_b = self._context("human:B")
        self._grant(ctx_b, "grant:b", ACTIONS)
        with self._socket():
            with self.assertRaises(AuthorityError) as caught:
                self._lease(grant_ref="grant:b").acquire(
                    context_ref=self.ctx_a["context_ref"], project=PROJECT,
                    item_ref=ITEM, ttl_s=30.0, now=time.time())
        self.assertEqual(caught.exception.reason_code, "UNTRUSTED_ACTOR")
        self._assert_no_lease()

    def test_foreign_os_principal_is_refused(self):
        client = _HandleClient(
            self.daemon, {"pid": 1234, "uid": 99999, "gid": 99999})
        with self.assertRaises(AuthorityError) as caught:
            self._lease(client=client).acquire(
                context_ref=self.ctx_a["context_ref"], project=PROJECT,
                item_ref=ITEM, ttl_s=30.0, now=time.time())
        self.assertEqual(caught.exception.reason_code, "OS_PRINCIPAL_MISMATCH")
        self._assert_no_lease()

    def test_missing_peer_is_refused(self):
        client = _HandleClient(self.daemon, None)
        with self.assertRaises(AuthorityError) as caught:
            self._lease(client=client).acquire(
                context_ref=self.ctx_a["context_ref"], project=PROJECT,
                item_ref=ITEM, ttl_s=30.0, now=time.time())
        self.assertEqual(caught.exception.reason_code, "UNTRUSTED_ACTOR")
        self._assert_no_lease()

    # -- live grant scope / window / revocation ---------------------------

    def test_wrong_project_is_refused(self):
        self._acquire_refused("POLICY_DENIED", project="other")

    def test_wrong_item_is_refused(self):
        self._acquire_refused("POLICY_DENIED", item_ref="SRV-9999")

    def test_grant_without_lease_acquire_is_refused(self):
        self._grant(self.ctx_a, "grant:no-acquire", ("lease.renew",))
        self._acquire_refused("POLICY_DENIED", grant_ref="grant:no-acquire")

    def test_expired_grant_is_refused(self):
        self._grant(self.ctx_a, "grant:expired", ACTIONS,
                    valid_from_offset=-600, expires_in=-60)
        self._acquire_refused("POLICY_DENIED", grant_ref="grant:expired")

    def test_revoked_grant_is_refused(self):
        self._grant(self.ctx_a, "grant:revoked", ACTIONS)
        self.daemon.control_store().revoke_grant(
            "grant:revoked", at=self._iso(), reason="test")
        self._acquire_refused("POLICY_DENIED", grant_ref="grant:revoked")

    def test_legacy_epochless_grant_is_unknown_and_not_backfilled(self):
        self._legacy_grant(self.ctx_a, "grant:legacy")
        caught = self._acquire_refused("INVALID_CONTEXT",
                                       grant_ref="grant:legacy")
        self.assertIn("epoch", caught.detail)
        self.assertNotIn("epoch",
                         self.daemon.control_store().grants()["grant:legacy"],
                         "an epoch must never be backfilled")

    def test_old_epoch_is_refused(self):
        with self._socket():
            with self.assertRaises(AuthorityError) as caught:
                self._lease().acquire(
                    context_ref=self.ctx_a["context_ref"], project=PROJECT,
                    item_ref=ITEM, ttl_s=30.0, now=time.time(),
                    expected_epoch=int(self.grant_a["epoch"]) - 1)
        self.assertEqual(caught.exception.reason_code, "EPOCH_STALE")
        self._assert_no_lease()

    # -- contention, fence and monotonic tokens ---------------------------

    def test_second_contender_is_refused_and_tokens_are_monotonic(self):
        with self._socket():
            first = self._lease().acquire(
                context_ref=self.ctx_a["context_ref"], project=PROJECT,
                item_ref=ITEM, ttl_s=30.0, now=time.time())

        ctx_b = self._context("human:B")
        self._grant(ctx_b, "grant:b", ACTIONS)
        with self._socket():
            with self.assertRaises(AuthorityError) as caught:
                self._lease(grant_ref="grant:b").acquire(
                    context_ref=ctx_b["context_ref"], project=PROJECT,
                    item_ref=ITEM, ttl_s=30.0, now=time.time())
        self.assertEqual(caught.exception.reason_code, "LEASE_HELD_BY_OTHER")
        self.assertEqual(self.we.active_lease(RESOURCE, now=time.time())["token"],
                         first["token"], "the contender must not move the fence")

        with self._socket():
            self._lease().release(
                context_ref=self.ctx_a["context_ref"], project=PROJECT,
                item_ref=ITEM, token=first["token"], now=time.time())
        with self._socket():
            second = self._lease(grant_ref="grant:b").acquire(
                context_ref=ctx_b["context_ref"], project=PROJECT,
                item_ref=ITEM, ttl_s=30.0, now=time.time())
        self.assertGreater(second["token"], first["token"])

    def test_renew_and_release_are_fenced(self):
        with self._socket():
            got = self._lease().acquire(
                context_ref=self.ctx_a["context_ref"], project=PROJECT,
                item_ref=ITEM, ttl_s=30.0, now=time.time())
        token = got["token"]

        with self._socket():
            with self.assertRaises(AuthorityError) as caught:
                self._lease().renew(
                    context_ref=self.ctx_a["context_ref"], project=PROJECT,
                    item_ref=ITEM, token=token + 1, ttl_s=60.0,
                    now=time.time())
        self.assertEqual(caught.exception.reason_code, "STALE_TOKEN")

        with self._socket():
            renewed = self._lease().renew(
                context_ref=self.ctx_a["context_ref"], project=PROJECT,
                item_ref=ITEM, token=token, ttl_s=60.0, now=time.time())
        self.assertEqual(renewed["token"], token)

        with self._socket():
            with self.assertRaises(AuthorityError) as caught:
                self._lease().release(
                    context_ref=self.ctx_a["context_ref"], project=PROJECT,
                    item_ref=ITEM, token=token + 1, now=time.time())
        self.assertEqual(caught.exception.reason_code, "STALE_TOKEN")

        with self._socket():
            released = self._lease().release(
                context_ref=self.ctx_a["context_ref"], project=PROJECT,
                item_ref=ITEM, token=token, now=time.time())
        self.assertTrue(released["released"])
        self._assert_no_lease()

    # -- the adapter never wraps a caller's JSON into a grant -------------

    def test_a_verified_answer_without_a_grant_is_refused(self):
        with self.assertRaises(AuthorityError) as caught:
            self._lease(client=_NoGrantClient()).acquire(
                context_ref=self.ctx_a["context_ref"], project=PROJECT,
                item_ref=ITEM, ttl_s=30.0, now=time.time())
        self.assertEqual(caught.exception.reason_code, "UNTRUSTED_ACTOR")
        self._assert_no_lease()

    def test_a_grant_that_is_not_the_bound_grant_is_refused(self):
        with self.assertRaises(AuthorityError) as caught:
            self._lease(client=_WrongGrantClient()).acquire(
                context_ref=self.ctx_a["context_ref"], project=PROJECT,
                item_ref=ITEM, ttl_s=30.0, now=time.time())
        self.assertEqual(caught.exception.reason_code, "INVALID_CONTEXT")
        self._assert_no_lease()

    def test_missing_socket_is_blocked_trust_source(self):
        client = UnixSocketVerifyClient(self.root / "run" / "absent.sock")
        with self.assertRaises(AuthorityError) as caught:
            self._lease(client=client).acquire(
                context_ref=self.ctx_a["context_ref"], project=PROJECT,
                item_ref=ITEM, ttl_s=30.0, now=time.time())
        self.assertEqual(caught.exception.reason_code, "BLOCKED_TRUST_SOURCE")
        self._assert_no_lease()

    # -- no caller identity / no public write verb ------------------------

    def test_adapter_signatures_do_not_accept_identity_or_epoch(self):
        forbidden = {"owner", "os_user", "actions", "epoch", "token", "verifier"}
        for method in (ServerOSLeaseAuthority.__init__,
                       ServerOSLeaseAuthority.verify,
                       UnixSocketVerifyClient.verify):
            names = set(inspect.signature(method).parameters)
            self.assertFalse(names & forbidden,
                             f"{method.__qualname__} accepts {names & forbidden}")

    def test_a_relative_verify_socket_is_refused(self):
        """No default and no environment fallback: the path must be explicit."""
        with self.assertRaises(ValueError):
            UnixSocketVerifyClient("serveros.sock")

    def test_an_empty_grant_ref_is_refused_at_construction(self):
        with self.assertRaises(ValueError):
            ServerOSLeaseAuthority(
                UnixSocketVerifyClient(self.sockpath), grant_ref="")

    def test_a_grant_with_a_boolean_epoch_is_refused(self):
        client = _AnswerClient(_verified_grant(epoch=True))
        with self.assertRaises(AuthorityError) as caught:
            self._lease(client=client).acquire(
                context_ref=self.ctx_a["context_ref"], project=PROJECT,
                item_ref=ITEM, ttl_s=30.0, now=time.time())
        self.assertEqual(caught.exception.reason_code, "UNTRUSTED_ACTOR")
        self._assert_no_lease()

    def test_a_grant_with_an_unreadable_window_is_refused(self):
        client = _AnswerClient(_verified_grant(valid_from="not-a-time"))
        with self.assertRaises(AuthorityError) as caught:
            self._lease(client=client).acquire(
                context_ref=self.ctx_a["context_ref"], project=PROJECT,
                item_ref=ITEM, ttl_s=30.0, now=time.time())
        self.assertEqual(caught.exception.reason_code, "UNTRUSTED_ACTOR")
        self._assert_no_lease()

    def test_no_lease_write_verb_is_exposed(self):
        from workengine.cli import build_parser
        from workengine.mcp import tool_names
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["lease", "acquire"])
        self.assertFalse([name for name in tool_names() if "lease" in name])


if __name__ == "__main__":
    unittest.main()
