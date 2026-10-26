"""Real owner ledger/Work SQLite paths; fixture identities, no live services.

POSIX socket cases retain the kernel peer. handle() cases explicitly use a
synthetic peer for refusal/race/crash fault injection and claim no OS identity.
"""
import os

import pytest

if os.name == "nt":
    pytest.skip("POSIX_ONLY: real fcntl control ledger and SO_PEERCRED ingress",
                allow_module_level=True)

import json
import multiprocessing
import socket
import threading
from contextlib import contextmanager

import test_owner_policy_binding as support
from workengine.model import WorkItem
from workengine.store import ServerOSStore


def make_world(tmp_path):
    work = ServerOSStore(tmp_path / "work")
    work.register_project("serveros", "SRV")
    work.create_item(WorkItem(id="SRV-0001", project="serveros", title="fixture",
                              kind="feature", card_locator="fixture.md"))
    issuer = support.owner_policy_mod.OwnerPolicyIssuer(
        os_principal=f"uid:{os.getuid()}", actor_ref="human:A",
        human_control_provider="provider:botmux-human-control")
    daemon = support.daemon_mod.Daemon(
        support.a_registry(), state_dir=tmp_path / "state",
        socket_path=tmp_path / "owner.sock", log=lambda _: None,
        owner_policy_issuer=issuer, owner_work_store=work)
    context = support.a_context(daemon)
    policy = {"policy_ref": "policy:owned", "os_principal": f"uid:{os.getuid()}",
              "actor_ref": "human:A", "projects": ["serveros"],
              "items": ["SRV-0001"],
              "actions": ["lease.acquire", "lease.renew", "lease.release", "work.commit"],
              "valid_from": support.iso(daemon, -60), "expires_at": support.iso(daemon, 600)}
    return daemon, work, context, policy


def call(daemon, method, params, *, peer=True):
    answer = daemon.handle({"method": method, "params": params},
                           peer=support.a_peer() if peer is True else peer)
    assert answer["ok"], answer
    return answer["result"]


def issue(world):
    daemon, work, context, policy = world
    result = call(daemon, "agent.owner.policy.issue", {
        "context_ref": context["context_ref"], "operation_id": "issue:1", "policy": policy})
    assert result["committed"], result
    stored = support.owner_policy_mod.OwnerPolicy(**result["policy"])
    grant = support.a_bound_grant(daemon, context, policy=stored,
                                 actions=tuple(policy["actions"]))
    return {"context_ref": context["context_ref"], "grant_ref": grant["grant_ref"],
            "project": "serveros", "item_ref": "SRV-0001",
            "expected_epoch": grant["epoch"], "operation_id": "acquire:1",
            "expected_store_binding": work.owner_target_info()["store_binding"],
            "verb": "lease.acquire", "ttl_s": 120}


def revoke(world, *, epoch=1):
    daemon, _, context, _ = world
    return call(daemon, "agent.owner.policy.revoke", {
        "context_ref": context["context_ref"], "policy_ref": "policy:owned",
        "expected_epoch": epoch, "operation_id": "revoke:1"})


def test_unconfigured_owner_issuer_refuses_without_writing(tmp_path):
    daemon = support.a_daemon(tmp_path)
    context = support.a_context(daemon)
    result = call(daemon, "agent.owner.policy.issue", {
        "context_ref": context["context_ref"], "operation_id": "issue:1", "policy": {}})
    assert result["reason"] == "BLOCKED_OWNER_ISSUER"
    assert not daemon.control_store()._read().get("owner_policies")


@pytest.mark.parametrize("fault", ["peer", "actor", "provider", "stale", "unverified"])
def test_policy_issuance_requires_exact_trusted_issuer(tmp_path, fault):
    world = make_world(tmp_path)
    daemon, _, context, policy = world
    if fault in ("actor", "unverified"):
        context = support.a_context(daemon, actor="human:B" if fault == "actor" else "human:A",
                                    status="BLOCKED" if fault == "unverified" else "VERIFIED",
                                    provenance="UNKNOWN" if fault == "unverified" else "FACT")
        if fault == "unverified":
            support.actor_mod.validate(context)
            assert support.actor_mod.authorizes(context, support.iso(daemon))["authorizes"] is False
    elif fault in ("provider", "stale"):
        context = dict(context)
        if fault == "provider":
            context["human_control_provider"] = "provider:other"
        else:
            context["expires_at"] = support.iso(daemon, -1)
        daemon.control_store().put_context(context)
    result = call(daemon, "agent.owner.policy.issue", {
        "context_ref": context["context_ref"], "operation_id": "issue:1", "policy": policy},
        peer=None if fault == "peer" else True)
    assert result["committed"] is False
    assert not daemon.control_store()._read().get("owner_policies")
    assert not daemon.control_store()._read().get("owner_policy_receipts")
    if fault == "unverified":
        assert result["reason"] == "UNTRUSTED_ACTOR"


def test_policy_receipt_replay_cas_and_revoke_survive_reopen(tmp_path):
    world = make_world(tmp_path)
    request = issue(world)
    daemon, _, context, policy = world
    replay = call(daemon, "agent.owner.policy.issue", {
        "context_ref": context["context_ref"], "operation_id": "issue:1", "policy": policy})
    assert replay["replayed"] is True
    assert replay["policy"]["epoch"] == 1
    assert revoke(world, epoch=99)["committed"] is False
    assert revoke(world)["committed"] is True
    reopened = support.daemon_mod.agent_control_store_mod.AgentControlStore(daemon.control_store().path)
    assert reopened._read()["owner_policies"]["policy:owned"]["revoked"] is True
    assert call(daemon, "agent.work.mutate", request)["committed"] is False


def test_atomic_acquire_and_transition_receipts_replay_once(tmp_path):
    world = make_world(tmp_path)
    request = issue(world)
    daemon, work, _, _ = world
    acquired = call(daemon, "agent.work.mutate", request)
    assert acquired["committed"], acquired
    replay = call(daemon, "agent.work.mutate", request)
    assert replay["replayed"] is True
    assert replay["result"]["token"] == acquired["result"]["token"]
    transition = {k: v for k, v in request.items() if k != "ttl_s"}
    transition.update(verb="item.transition", operation_id="transition:1",
                      token=acquired["result"]["token"], target="AVAILABLE")
    assert call(daemon, "agent.work.mutate", transition)["committed"] is True
    assert call(daemon, "agent.work.mutate", transition)["replayed"] is True
    assert work.get_item("SRV-0001").state.value == "AVAILABLE"
    with work.connection() as conn:
        assert conn.execute("SELECT count(*) FROM item_events WHERE event_type='transitioned'").fetchone()[0] == 1
    assert revoke(world)["committed"] is True
    assert call(daemon, "agent.work.mutate", transition)["committed"] is False


@pytest.mark.parametrize("verb", ["renew", "release", "transition", "raw_update", "raw_delete"])
def test_old_store_paths_cannot_bypass_managed_item_fence(tmp_path, verb):
    world = make_world(tmp_path)
    request = issue(world)
    daemon, work, _, _ = world
    acquired = call(daemon, "agent.work.mutate", request)
    assert acquired["committed"], acquired
    token = acquired["result"]["token"]
    with pytest.raises(Exception, match="OWNER_COMMIT_FENCE"):
        if verb == "renew":
            work.renew_lease("work-write:SRV-0001", owner="human:A", token=token, ttl_s=60)
        elif verb == "release":
            work.release_lease("work-write:SRV-0001", owner="human:A", token=token)
        elif verb == "transition":
            work.transition_item("SRV-0001", "AVAILABLE")
        else:
            with work.transaction(immediate=True) as conn:
                conn.execute("UPDATE items_index SET title='bypass' WHERE item_id='SRV-0001'"
                             if verb == "raw_update" else
                             "DELETE FROM owner_operation_receipts WHERE item_ref='SRV-0001'")


@pytest.mark.parametrize("change", ["policy_revoke", "policy_reissue", "grant_revoke"])
def test_revoke_waits_for_actual_acquire_transaction_then_fences_writer(tmp_path, monkeypatch, change):
    world = make_world(tmp_path)
    request = issue(world)
    daemon, work, _, _ = world
    inside, release, revoked = threading.Event(), threading.Event(), threading.Event()
    original = work.transaction

    @contextmanager
    def held(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            yield conn
            inside.set()
            assert release.wait(5)

    monkeypatch.setattr(work, "transaction", held)
    results = {}
    writer = threading.Thread(target=lambda: results.update(acquire=call(daemon, "agent.work.mutate", request)))
    writer.start()
    assert inside.wait(5)

    def revoke_in_thread():
        if change == "policy_revoke":
            results["revoke"] = revoke(world)
        elif change == "policy_reissue":
            results["revoke"] = call(daemon, "agent.owner.policy.reissue", {
                "context_ref": world[2]["context_ref"], "operation_id": "reissue:1",
                "expected_epoch": 1, "policy": world[3]})
        else:
            daemon.control_store().revoke_grant(request["grant_ref"], at=support.iso(daemon))
            results["revoke"] = {"committed": True}
        revoked.set()

    revoker = threading.Thread(target=revoke_in_thread)
    revoker.start()
    assert not revoked.wait(0.05), "policy revoke escaped the writer's control fence"
    release.set()
    writer.join(5)
    revoker.join(5)
    assert results["acquire"]["committed"] and results["revoke"]["committed"]
    assert not call(daemon, "agent.work.mutate", {**request, "operation_id": "acquire:2"})["committed"]


@pytest.mark.parametrize("boundary,expected", [("before_commit", 0), ("after_commit", 1)])
def test_acquire_crash_reconciles_same_receipt_and_lease(tmp_path, boundary, expected):
    world = make_world(tmp_path)
    request = issue(world)
    daemon, work, _, _ = world

    def child():
        original = work.transaction

        @contextmanager
        def crash(*args, **kwargs):
            with original(*args, **kwargs) as conn:
                yield conn
                if boundary == "before_commit":
                    os._exit(73)
            os._exit(73)

        work.transaction = crash
        call(daemon, "agent.work.mutate", request)

    process = multiprocessing.get_context("fork").Process(target=child)
    process.start()
    process.join(10)
    assert process.exitcode == 73
    with work.connection() as conn:
        assert conn.execute("SELECT count(*) FROM owner_operation_receipts").fetchone()[0] == expected
        assert conn.execute("SELECT count(*) FROM leases").fetchone()[0] == expected
    retry = call(daemon, "agent.work.mutate", request)
    assert retry["committed"]
    assert retry["replayed"] is bool(expected)


def test_owner_client_preserves_real_kernel_socket_peer(tmp_path):
    from workengine import serveros_authority
    world = make_world(tmp_path)
    request = issue(world)
    daemon, work, _, _ = world
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(daemon.socket_path))
    server.listen(1)

    def serve():
        connection, _ = server.accept()
        with connection:
            daemon._serve_connection(connection)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        client = serveros_authority.UnixSocketOwnerClient(daemon.socket_path)
        result = client.mutate(**request)
        assert result["committed"], result
        assert result["receipt"]["os_principal"] == f"uid:{os.getuid()}"
        assert work.active_lease("work-write:SRV-0001")["owner"] == "human:A"
    finally:
        thread.join(5)
        server.close()


def test_private_bootstrap_file_requires_private_owner_file(tmp_path):
    path = tmp_path / "issuer.json"
    document = {"schema": "serveros/owner-policy-issuer.v1", "os_principal": f"uid:{os.getuid()}",
                "actor_ref": "human:A", "human_control_provider": "provider:botmux-human-control"}
    path.write_text(json.dumps(document))
    path.chmod(0o600)
    assert support.owner_policy_mod.load_issuer(path).actor_ref == "human:A"
    path.chmod(0o644)
    with pytest.raises(ValueError, match="UNSAFE_FILE"):
        support.owner_policy_mod.load_issuer(path)
    path.chmod(0o600)
    link = tmp_path / "issuer-link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="UNSAFE_FILE"):
        support.owner_policy_mod.load_issuer(link)


def test_control_only_commit_keeps_binding_and_receipt_in_json(tmp_path):
    world = make_world(tmp_path)
    request = issue(world)
    daemon, work, _, _ = world
    acquired = call(daemon, "agent.work.mutate", request)
    operation = {key: value for key, value in request.items() if key not in ("ttl_s", "verb", "expected_store_binding")}
    operation.update(operation_id="binding:1", token=acquired["result"]["token"],
                     intent={"checkpoint_ref": "checkpoint:fixture", "cycle_ref": "cycle:fixture"})

    def mutation(document):
        document["admissions"]["cycle:fixture"] = {"fixture": "bound"}
        return {"bound": True}

    owner = daemon.owner_work_operations()
    result = owner.control_commit(operation, peer=support.a_peer(), mutation=mutation)
    assert result["committed"] and not result["replayed"]
    stored = daemon.control_store()._read()
    assert stored["admissions"]["cycle:fixture"] == {"fixture": "bound"}
    assert stored["owner_control_receipts"]["binding:1"]["result"] == {"bound": True}
    replay = owner.control_commit(operation, peer=support.a_peer(), mutation=lambda _: pytest.fail("replayed mutation"))
    assert replay["replayed"]
    assert revoke(world)["committed"]
    with pytest.raises(ValueError):
        owner.control_commit(operation, peer=support.a_peer(), mutation=mutation)


def test_signed_grant_issuance_uses_unique_existing_owner_policy(tmp_path, monkeypatch):
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    world = make_world(tmp_path)
    issue(world)
    daemon, _, context, policy = world
    actor = support.daemon_mod.actor_mod
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    trust = tmp_path / "provider-trust.json"
    policy_provider = context["human_control_provider"]
    trust.write_text(json.dumps({"schema": "serveros/human-control-trust.v1", "audience": "device:fixture",
        "providers": {policy_provider: {"keys": {"fixture": {
            "enabled": True, "public_key": base64.b64encode(public).decode()}}}}}))
    monkeypatch.setattr(actor, "DELEGATION_TRUST", trust)
    report = {key: context[key] for key in actor.PROVIDER_REPORT_FIELDS}
    grant = {"grant_ref": "grant:signed-fixture", "projects": policy["projects"], "items": policy["items"],
             "actions": policy["actions"], "valid_from": policy["valid_from"], "expires_at": policy["expires_at"],
             "scope_digests": {"SRV-0001": "sha256:" + "a" * 64},
             "budget": {"wall_seconds": 120, "max_attempts": 1, "max_output_bytes": 1024, "exhausted": False}}
    payload = {"schema": actor.DELEGATION_EVIDENCE_SCHEMA, "provider_id": policy_provider,
               "key_id": "fixture", "audience": "device:fixture", "issued_at": support.iso(daemon),
               "actor_report": report, "grant": grant}
    evidence = {"payload": payload, "signature": base64.b64encode(private.sign(actor.signed_bytes(payload))).decode()}
    answer = call(daemon, "agent.grant.issue", {"evidence": evidence})
    assert answer["issued"], answer
    stored = daemon.control_store().get_grant(grant["grant_ref"])
    assert stored["owner_policy_ref"] == "policy:owned" and stored["owner_policy_epoch"] == 1
    assert revoke(world)["committed"]
    assert call(daemon, "agent.grant.issue", {"evidence": evidence})["issued"] is False
