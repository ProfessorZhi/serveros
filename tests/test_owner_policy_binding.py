"""Owner policy: kernel uid -> audited actor -> exact scope (#237 B3).

WHY THIS FILE EXISTS
--------------------
#272 proves a grant is live, carries a store-minted epoch, is bound to the
kernel peer uid of its issuing call, and is scoped. It does not prove the OWNER
authorized that uid to act as that principal for that project/Item/actions.
`runtime/owner_policy.py` is the typed, fail-closed adapter for that missing
fact. This file exercises it through the REAL daemon verify/issue routes.

EVIDENCE LEVELS
---------------
* `test_a_real_socket_peer_with_a_live_owner_policy_is_commit_fence_blocked`
  is **REAL_TEMP**: a temp AF_UNIX socket, the kernel's real `SO_PEERCRED`, and
  production daemon/store code. The context, grant and owner policy are TEST
  FIXTURES; there is no real provider signature and no production owner. The
  live policy passes every owner check, and the route still refuses mutation
  eligibility because no owner commit fence exists.
* The synthetic-peer case is **LOCAL**: peer metadata is passed to
  `daemon.handle`, so it is not OS evidence. It is labelled SYNTHETIC_PEER.
* Two real uids are **BLOCKED_MULTIPRINCIPAL_NOT_RUN**: this process is uid
  1002 and has no passwordless sudo/setuid, so a second real uid cannot be
  attested. The test skips with that exact reason rather than faking it.
"""
from __future__ import annotations

import os

import pytest

# POSIX-ONLY, and before any POSIX import: this module loads
# `runtime/daemon.py` at module scope, which imports `fcntl`; the tests use
# AF_UNIX, `SO_PEERCRED` and `uid`. On Windows the module cannot be collected
# at all. Only `os.name == "nt"` is skipped; Linux keeps every assertion below.
if os.name == "nt":
    pytest.skip(
        "POSIX_ONLY: runtime/daemon.py imports fcntl and these tests use "
        "AF_UNIX/SO_PEERCRED/uid; this module cannot be collected on Windows",
        allow_module_level=True)

import importlib.util
import json
import socket
import subprocess
import sys
import threading
from pathlib import Path

import owner_policy_fixtures as opf

REPO = Path(__file__).resolve().parent.parent
ITEM = "SRV-0001"
PROJECT = "serveros"
ACTION = "lease.acquire"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


daemon_mod = _load("serveros_runtime_daemon", REPO / "runtime" / "daemon.py")
registry_mod = _load("serveros_runtime_registry", REPO / "runtime" / "registry.py")
actor_mod = _load("_serveros_context_actor", REPO / "context" / "actor.py")
owner_policy_mod = _load("serveros_runtime_owner_policy",
                         REPO / "runtime" / "owner_policy.py")


def a_registry():
    return registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:server-beijing",
        "resources": [],
    })


def iso(daemon, offset_s: float = 0.0) -> str:
    return daemon_mod._iso(daemon._now() + offset_s)


def a_context(daemon, *, actor="human:A", status="VERIFIED",
              provenance="FACT") -> dict:
    now = iso(daemon)
    record = actor_mod.build({
        "human_control_provider": "provider:botmux-human-control",
        "transport": "feishu",
        "transport_identity": "botmux-verified-enterprise-user",
        "conversation_ref": "oc_owner_policy",
        "message_ref": "om_owner_policy",
        "observed_at": now,
        "provider_status": status,
        "provenance": provenance,
        "actor_ref": actor,
        "provider_pid": 4242,
    }, now=now)
    daemon.control_store().put_context(record)
    return record


def a_bound_grant(daemon, context, *, ref="grant:1", policy, uid=None,
                  projects=(PROJECT,), items=(ITEM,), actions=(ACTION,),
                  with_binding=True) -> dict:
    store = daemon.control_store()
    record = {
        "grant_ref": ref,
        "delegated_by": context["context_ref"],
        "valid_from": iso(daemon, -60),
        "expires_at": iso(daemon, 600),
        "projects": list(projects),
        "items": list(items),
        "actions": list(actions),
        "revoked": False,
        "epoch": store._next_grant_epoch(),
        "os_principal": f"uid:{os.getuid() if uid is None else uid}",
        "actor_ref": context["actor_ref"],
    }
    if with_binding:
        record.update({
            "owner_policy_ref": policy.policy_ref,
            "owner_policy_epoch": policy.epoch,
            "owner_policy_issued_by": policy.issued_by,
            "owner_policy_issued_at": policy.issued_at,
        })
    with store._mutation():
        store._data["grants"][ref] = record
        store._flush()
    return record


def a_daemon(tmp_path, *, resolver=None, state_name="state",
             socket_name="serveros.sock"):
    return daemon_mod.Daemon(
        a_registry(), state_dir=tmp_path / state_name,
        socket_path=tmp_path / "run" / socket_name,
        log=lambda message: None, owner_policy_resolver=resolver)


def a_peer(uid=None):
    return {"pid": os.getpid(), "uid": os.getuid() if uid is None else uid,
            "gid": os.getgid()}


def verify(daemon, *, context_ref, grant_ref="grant:1", action=ACTION,
           project=PROJECT, item_ref=ITEM, peer_value=None, expected_epoch=None):
    params = {"context_ref": context_ref, "grant_ref": grant_ref,
              "action": action, "project": project, "item_ref": item_ref}
    if expected_epoch is not None:
        params["expected_epoch"] = expected_epoch
    return daemon.handle(
        {"method": "agent.lease.authority.verify", "params": params},
        peer=a_peer() if peer_value is None else peer_value)["result"]


def verify_over_socket(daemon, params) -> dict:
    """One real AF_UNIX connection served by the real daemon."""
    path = daemon.socket_path
    path.parent.mkdir(parents=True, exist_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.settimeout(5)
    server.bind(str(path))
    server.listen(1)

    def serve_once():
        try:
            connection, _ = server.accept()
        except OSError:
            return
        try:
            daemon._serve_connection(connection)
        finally:
            connection.close()

    thread = threading.Thread(target=serve_once, daemon=True)
    thread.start()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5)
    try:
        client.connect(str(path))
        client.sendall(json.dumps({
            "method": "agent.lease.authority.verify",
            "params": params,
        }).encode() + b"\n")
        buffer = b""
        while b"\n" not in buffer:
            chunk = client.recv(65536)
            if not chunk:
                break
            buffer += chunk
        return json.loads(buffer.split(b"\n", 1)[0].decode())["result"]
    finally:
        client.close()
        thread.join(timeout=5)
        server.close()
        try:
            path.unlink()
        except OSError:
            pass


@pytest.fixture
def world(tmp_path):
    policy = opf.a_policy(os_principal=f"uid:{os.getuid()}",
                          actor_ref="human:A")
    resolver = opf.StaticOwnerPolicyResolver({policy.policy_ref: policy})
    daemon = a_daemon(tmp_path, resolver=resolver)
    context = a_context(daemon)
    grant = a_bound_grant(daemon, context, policy=policy)
    return {"daemon": daemon, "context": context, "grant": grant,
            "policy": policy, "resolver": resolver, "tmp_path": tmp_path}


def _params(world, **overrides):
    params = {"context_ref": world["context"]["context_ref"],
              "grant_ref": world["grant"]["grant_ref"],
              "action": ACTION, "project": PROJECT, "item_ref": ITEM}
    params.update(overrides)
    return params


# ---------------------------------------------------------------------------
# REAL_TEMP: the real AF_UNIX peer + real daemon route.
# ---------------------------------------------------------------------------

def test_a_real_socket_peer_with_a_live_owner_policy_is_commit_fence_blocked(
        world):
    # The owner checks pass, but a point-in-time read is not a write grant:
    # without an owner-issued atomic policy/epoch commit fence the route
    # refuses mutation eligibility. The audit stays for the read-only record.
    answer = verify_over_socket(world["daemon"], _params(world))
    assert answer["verified"] is False, answer
    assert answer["reason"] == "BLOCKED_OWNER_COMMIT_SEAM", answer
    assert answer["point_in_time"] is True, answer
    assert "grant" not in answer, answer
    audit = answer["owner_policy"]
    assert audit["policy_ref"] == "policy:test"
    assert audit["issued_by"] == "owner:test"
    assert audit["actor_ref"] == "human:A"
    assert audit["os_principal"] == f"uid:{os.getuid()}"


# ---------------------------------------------------------------------------
# Fail-closed: no resolver, unknown policy, unbound grant.
# ---------------------------------------------------------------------------

def test_no_owner_resolver_blocks_owner_policy(world):
    daemon = daemon_mod.Daemon(
        a_registry(), state_dir=world["daemon"].state_dir,
        socket_path=world["tmp_path"] / "run" / "nores.sock",
        log=lambda message: None, owner_policy_resolver=None)
    answer = verify(daemon, context_ref=world["context"]["context_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "BLOCKED_OWNER_POLICY"


def test_an_unknown_policy_blocks_owner_policy(world):
    daemon = daemon_mod.Daemon(
        a_registry(), state_dir=world["daemon"].state_dir,
        socket_path=world["tmp_path"] / "run" / "unknown.sock",
        log=lambda message: None,
        owner_policy_resolver=opf.StaticOwnerPolicyResolver({}))
    answer = verify(daemon, context_ref=world["context"]["context_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "BLOCKED_OWNER_POLICY"


def test_a_grant_without_an_owner_binding_blocks(world):
    legacy = a_bound_grant(world["daemon"], world["context"],
                           ref="grant:nobind", policy=world["policy"],
                           with_binding=False)
    answer = verify(world["daemon"],
                    context_ref=world["context"]["context_ref"],
                    grant_ref=legacy["grant_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "BLOCKED_OWNER_POLICY"


# ---------------------------------------------------------------------------
# The owner policy's own refusals.
# ---------------------------------------------------------------------------

def _with_policy(world, policy):
    return daemon_mod.Daemon(
        a_registry(), state_dir=world["daemon"].state_dir,
        socket_path=world["tmp_path"] / "run" / f"{policy.policy_ref}.sock",
        log=lambda message: None,
        owner_policy_resolver=opf.StaticOwnerPolicyResolver(
            {policy.policy_ref: policy}))


def _daemon_with(world, resolver, name):
    return daemon_mod.Daemon(
        a_registry(), state_dir=world["daemon"].state_dir,
        socket_path=world["tmp_path"] / "run" / f"{name}.sock",
        log=lambda message: None, owner_policy_resolver=resolver)


class RevokingGrantResolver:
    """The QA2 race: revoke the grant while the route resolves policy."""

    def __init__(self, store, grant_ref, policy):
        self.store = store
        self.grant_ref = grant_ref
        self.policy = policy

    def resolve(self, *, now=None, **_):
        self.store.revoke_grant(self.grant_ref, at=now, reason="test race")
        return self.policy


class ChangingPolicyResolver:
    """Return a valid policy, then a changed one on the second read."""

    def __init__(self, first, second):
        self.first = first
        self.second = second
        self.calls = 0

    def resolve(self, **_):
        self.calls += 1
        return self.first if self.calls == 1 else self.second


def test_a_revoked_policy_is_policy_denied(world):
    policy = opf.a_policy(os_principal=f"uid:{os.getuid()}",
                          actor_ref="human:A", revoked=True)
    answer = verify(_with_policy(world, policy),
                    context_ref=world["context"]["context_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "POLICY_DENIED"


def test_an_expired_policy_is_policy_denied(world):
    policy = opf.a_policy(os_principal=f"uid:{os.getuid()}",
                          actor_ref="human:A",
                          expires_at="2000-01-01T00:00:00Z")
    answer = verify(_with_policy(world, policy),
                    context_ref=world["context"]["context_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "POLICY_DENIED"


def test_a_stale_policy_epoch_blocks(world):
    policy = opf.a_policy(os_principal=f"uid:{os.getuid()}",
                          actor_ref="human:A", epoch=2)
    answer = verify(_with_policy(world, policy),
                    context_ref=world["context"]["context_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "BLOCKED_OWNER_POLICY"


def test_a_wrong_uid_policy_is_untrusted_actor(world):
    policy = opf.a_policy(os_principal="uid:99999", actor_ref="human:A")
    answer = verify(_with_policy(world, policy),
                    context_ref=world["context"]["context_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "UNTRUSTED_ACTOR"


def test_a_wrong_actor_policy_is_untrusted_actor(world):
    policy = opf.a_policy(os_principal=f"uid:{os.getuid()}",
                          actor_ref="human:B")
    answer = verify(_with_policy(world, policy),
                    context_ref=world["context"]["context_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "UNTRUSTED_ACTOR"


def test_an_out_of_scope_policy_is_policy_denied(world):
    policy = opf.a_policy(os_principal=f"uid:{os.getuid()}",
                          actor_ref="human:A", projects=("other",))
    answer = verify(_with_policy(world, policy),
                    context_ref=world["context"]["context_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "POLICY_DENIED"


def test_a_non_bool_revoked_policy_is_blocked(world):
    """A data error is not an owner decision: never read as unrevoked."""
    for value in (1, 0, "yes", "false", None):
        policy = opf.a_policy(os_principal=f"uid:{os.getuid()}",
                              actor_ref="human:A", revoked=value)
        answer = verify(_with_policy(world, policy),
                        context_ref=world["context"]["context_ref"])
        assert answer["verified"] is False, value
        assert answer["reason"] == "BLOCKED_OWNER_POLICY", (value, answer)


def test_a_future_issued_policy_is_blocked(world):
    policy = opf.a_policy(os_principal=f"uid:{os.getuid()}",
                          actor_ref="human:A",
                          issued_at="2999-01-01T00:00:00Z")
    answer = verify(_with_policy(world, policy),
                    context_ref=world["context"]["context_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "BLOCKED_OWNER_POLICY"


def test_a_grant_revoke_during_resolve_is_blocked_atomicity(world):
    store = world["daemon"].control_store()
    resolver = RevokingGrantResolver(store, "grant:1", world["policy"])
    daemon = _daemon_with(world, resolver, "race-grant")
    answer = verify(daemon, context_ref=world["context"]["context_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "BLOCKED_ATOMICITY"
    assert store.get_grant("grant:1")["revoked"] is True


def test_a_policy_change_during_resolve_is_blocked_atomicity(world):
    changed = opf.a_policy(os_principal=f"uid:{os.getuid()}",
                           actor_ref="human:A", revoked=True)
    resolver = ChangingPolicyResolver(world["policy"], changed)
    daemon = _daemon_with(world, resolver, "race-policy")
    answer = verify(daemon, context_ref=world["context"]["context_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "BLOCKED_ATOMICITY"


def test_a_live_policy_is_a_point_in_time_audit_not_a_verdict(world):
    answer = verify(world["daemon"],
                    context_ref=world["context"]["context_ref"])
    assert answer["verified"] is False, answer
    assert answer["reason"] == "BLOCKED_OWNER_COMMIT_SEAM", answer
    assert answer["point_in_time"] is True, answer
    assert answer["owner_policy"]["policy_ref"] == "policy:test"
    assert "grant" not in answer, answer


def test_a_non_verified_context_is_still_refused(world):
    # The owner policy must not bypass #318's strong context verdict. The grant
    # is delegated by the UNVERIFIED context so the context check is the one
    # that fires, not a delegated_by mismatch.
    context = a_context(world["daemon"], status="BLOCKED", provenance="UNKNOWN")
    a_bound_grant(world["daemon"], context, ref="grant:unverified",
                  policy=world["policy"])
    answer = verify(world["daemon"], context_ref=context["context_ref"],
                    grant_ref="grant:unverified")
    assert answer["verified"] is False
    assert answer["reason"] == "UNTRUSTED_ACTOR"


# ---------------------------------------------------------------------------
# Synthetic peer: LOCAL, explicitly not REAL_OS.
# ---------------------------------------------------------------------------

def test_a_synthetic_peer_is_not_real_os_evidence(world):
    """SYNTHETIC_PEER / LOCAL: not OS attestation, only the gate's input."""
    answer = verify(world["daemon"],
                    context_ref=world["context"]["context_ref"],
                    peer_value={"pid": 1234, "uid": 99999, "gid": 99999})
    assert answer["verified"] is False
    assert answer["reason"] == "UNTRUSTED_ACTOR"


def test_two_real_uids_are_blocked_multiprincipal_not_run():
    """BLOCKED_MULTIPRINCIPAL_NOT_RUN, with the environment stated."""
    if os.geteuid() == 0:
        pytest.skip("running as root; a second uid would not be a controlled "
                    "principal in this environment")
    try:
        probe = subprocess.run(["sudo", "-n", "true"], capture_output=True,
                               timeout=5)
        can_switch = probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        can_switch = False
    if can_switch:
        pytest.skip("passwordless sudo is available; a real second-uid run is "
                    "possible but not executed by this local slice")
    pytest.skip(
        "BLOCKED_MULTIPRINCIPAL_NOT_RUN: this test process is uid "
        f"{os.getuid()} and has no passwordless sudo/setuid, so a second real "
        "uid cannot be attested. Synthetic peer metadata covers only LOCAL "
        "negatives and is not REAL_OS evidence.")


# ---------------------------------------------------------------------------
# The mutating boundary: no grant without the owner policy.
# ---------------------------------------------------------------------------

def test_the_issue_route_refuses_without_an_owner_resolver(tmp_path):
    daemon = a_daemon(tmp_path, resolver=None)
    answer = daemon.handle(
        {"method": "agent.grant.issue", "params": {"evidence": {}}},
        peer=a_peer())["result"]
    assert answer["issued"] is False
    assert answer["reason"] == "BLOCKED_OWNER_POLICY"
    assert daemon.control_store().grants() == {}


def test_the_store_issue_binds_the_owner_policy_atomically(tmp_path, monkeypatch):
    policy = opf.a_policy(os_principal=f"uid:{os.getuid()}",
                          actor_ref="human:A")
    # The issuance path has no grant ref yet; the owner source resolves by the
    # facts, so the fixture returns its default policy.
    resolver = opf.StaticOwnerPolicyResolver({}, default=policy)
    daemon = a_daemon(tmp_path, resolver=resolver)
    context = a_context(daemon)
    approved = {
        "grant_ref": "grant:issued",
        "delegated_by": context["context_ref"],
        "valid_from": iso(daemon, -60),
        "expires_at": iso(daemon, 600),
        "projects": [PROJECT], "items": [ITEM], "actions": [ACTION],
    }
    monkeypatch.setattr(daemon_mod.actor_mod, "verify_delegation",
                        lambda evidence, *, now: (approved, context))
    record = daemon.control_store().issue_grant(
        {}, now=iso(daemon), os_principal=f"uid:{os.getuid()}",
        owner_policy_resolver=resolver)
    assert record["owner_policy_ref"] == "policy:test"
    assert record["owner_policy_epoch"] == 1
    assert record["owner_policy_issued_by"] == "owner:test"
    assert record["actor_ref"] == "human:A"
    assert record["os_principal"] == f"uid:{os.getuid()}"
    assert daemon.control_store().get_grant("grant:issued")["owner_policy_ref"] \
        == "policy:test"


def _approved_with_forged_owner_fields(context):
    return {
        "grant_ref": "grant:forged",
        "delegated_by": context["context_ref"],
        "valid_from": "2000-01-01T00:00:00Z",
        "expires_at": "2999-01-01T00:00:00Z",
        "projects": [PROJECT], "items": [ITEM], "actions": [ACTION],
        "owner_policy_ref": "policy:forged",
        "owner_policy_epoch": 9,
        "owner_policy_issued_by": "owner:forged",
        "owner_policy_issued_at": "2026-01-01T00:00:00Z",
        "actor_ref": "human:B",
    }


def test_issue_grant_drops_approved_owner_fields(tmp_path, monkeypatch):
    policy = opf.a_policy(os_principal=f"uid:{os.getuid()}",
                          actor_ref="human:A")
    resolver = opf.StaticOwnerPolicyResolver({}, default=policy)
    daemon = a_daemon(tmp_path, resolver=resolver)
    context = a_context(daemon)
    approved = _approved_with_forged_owner_fields(context)
    monkeypatch.setattr(daemon_mod.actor_mod, "verify_delegation",
                        lambda evidence, *, now: (dict(approved), dict(context)))
    record = daemon.control_store().issue_grant(
        {}, now=iso(daemon), os_principal=f"uid:{os.getuid()}",
        owner_policy_resolver=resolver)
    assert record["owner_policy_ref"] == "policy:test"
    assert record["owner_policy_epoch"] == 1
    assert record["actor_ref"] == "human:A"
    assert "refused_owner_fields" in record["issuance"]
    assert "owner_policy_ref" in record["issuance"]["refused_owner_fields"]


def test_issue_grant_without_resolver_drops_forged_owner_fields(
        tmp_path, monkeypatch):
    daemon = a_daemon(tmp_path, resolver=None)
    context = a_context(daemon)
    approved = _approved_with_forged_owner_fields(context)
    monkeypatch.setattr(daemon_mod.actor_mod, "verify_delegation",
                        lambda evidence, *, now: (dict(approved), dict(context)))
    record = daemon.control_store().issue_grant(
        {}, now=iso(daemon), os_principal=f"uid:{os.getuid()}",
        owner_policy_resolver=None)
    assert "owner_policy_ref" not in record
    assert "owner_policy_epoch" not in record
    assert "actor_ref" not in record
    assert "refused_owner_fields" in record["issuance"]
    # The forged binding is not authority: a resolver-bound verify refuses.
    policy = opf.a_policy(os_principal=f"uid:{os.getuid()}",
                          actor_ref="human:A")
    checker = daemon_mod.Daemon(
        a_registry(), state_dir=daemon.state_dir,
        socket_path=tmp_path / "run" / "checker.sock",
        log=lambda message: None,
        owner_policy_resolver=opf.StaticOwnerPolicyResolver(
            {"policy:forged": policy}))
    answer = verify(checker, context_ref=context["context_ref"],
                    grant_ref="grant:forged")
    assert answer["verified"] is False
    assert answer["reason"] == "BLOCKED_OWNER_POLICY"


def test_idempotent_issue_readback_rechecks_the_owner_policy(
        tmp_path, monkeypatch):
    policy = opf.a_policy(os_principal=f"uid:{os.getuid()}",
                          actor_ref="human:A")
    resolver = opf.StaticOwnerPolicyResolver({}, default=policy)
    daemon = a_daemon(tmp_path, resolver=resolver)
    context = a_context(daemon)
    approved = {
        "grant_ref": "grant:readback",
        "delegated_by": context["context_ref"],
        "valid_from": iso(daemon, -60),
        "expires_at": iso(daemon, 600),
        "projects": [PROJECT], "items": [ITEM], "actions": [ACTION],
    }
    evidence = {"payload": {"grant": {"grant_ref": "grant:readback"}}}
    monkeypatch.setattr(daemon_mod.actor_mod, "verify_delegation",
                        lambda evidence, *, now: (dict(approved), dict(context)))
    first = daemon.handle(
        {"method": "agent.grant.issue", "params": {"evidence": evidence}},
        peer=a_peer())["result"]
    assert first["issued"] is True
    daemon._owner_policy_resolver = opf.StaticOwnerPolicyResolver(
        {}, default=opf.a_policy(os_principal=f"uid:{os.getuid()}",
                                 actor_ref="human:A", revoked=True))
    second = daemon.handle(
        {"method": "agent.grant.issue", "params": {"evidence": evidence}},
        peer=a_peer())["result"]
    assert second["issued"] is False
    assert second["reason"] == "POLICY_DENIED"
    stored = daemon.control_store().get_grant("grant:readback")
    assert stored["owner_policy_ref"] == "policy:test"
    assert stored["revoked"] is False


def test_the_store_issue_writes_nothing_when_the_policy_is_unknown(
        tmp_path, monkeypatch):
    daemon = a_daemon(tmp_path,
                      resolver=opf.StaticOwnerPolicyResolver({}))
    context = a_context(daemon)
    approved = {
        "grant_ref": "grant:refused",
        "delegated_by": context["context_ref"],
        "valid_from": iso(daemon, -60),
        "expires_at": iso(daemon, 600),
        "projects": [PROJECT], "items": [ITEM], "actions": [ACTION],
    }
    monkeypatch.setattr(daemon_mod.actor_mod, "verify_delegation",
                        lambda evidence, *, now: (approved, context))
    with pytest.raises(daemon_mod.agent_control_store_mod.StoreError) as caught:
        daemon.control_store().issue_grant(
            {}, now=iso(daemon), os_principal=f"uid:{os.getuid()}",
            owner_policy_resolver=opf.StaticOwnerPolicyResolver({}))
    assert "BLOCKED_OWNER_POLICY" in str(caught.value)
    assert daemon.control_store().get_grant("grant:refused") is None
