"""Trusted identity labels and correlation context never confer authority."""
from dataclasses import replace
import hashlib
import json
import os

import pytest

from fleet_mcp.config import load_config
from fleet_mcp.errors import FleetRefusal
from fleet_mcp.fleet import FleetService, Principal
from fleet_mcp.http_transport import HttpSurface, build_oauth
from fleet_mcp.runtime import owner_principal
from fleet_mcp.run_watcher import RunWatcher
from fleet_mcp.tools import ToolContext, call_tool
from test_fleet_create_session import Harness, TOKYO
from test_fleet_dispatch_claims import owner_store
from test_fleet_events import event, store
from test_fleet_mcp_surface import Harness as SendHarness
from fleet_mcp.identity import SessionKey


REFS = {"work_ref": "work:17", "parent_ref": "checkpoint:4", "agent_ref": "agent:3"}


def mapped_config(h, **entries):
    return replace(h.config, auth=replace(h.config.auth, principals=entries))


def test_old_principal_and_unmapped_owner_credentials_remain_clients(tmp_path):
    # An unmapped credential must not be silently promoted by its name.
    h = Harness(tmp_path)
    p = Principal("old", frozenset({"fleet:read"}), "oauth")
    assert (p.subject, p.kind, p.name) == ("old", "client", "old")
    assert owner_principal(h.config).kind == "client"
    surface = HttpSurface(service=h.service, config=h.config)
    assert surface.principal_for_token("owner-token-value").kind == "client"


def test_configured_owner_aliases_share_subject_keep_source_and_scopes(tmp_path):
    h = Harness(tmp_path)
    config = mapped_config(h, **{
        "owner-pat": {"subject": "owner:vincent", "kind": "owner"},
        "local-owner": {"subject": "owner:vincent", "kind": "owner"}})
    remote = HttpSurface(service=h.service, config=config).principal_for_token("owner-token-value")
    local = owner_principal(config)
    assert (remote.subject, local.subject, remote.kind, local.kind) == (
        "owner:vincent", "owner:vincent", "owner", "owner")
    assert (remote.name, local.name) == ("owner-pat", "local-owner")
    assert remote.scopes == HttpSurface(service=h.service, config=h.config).principal_for_token(
        "owner-token-value").scopes
    # Kind does not supply a send scope or a device/project grant.
    denied = Principal("ungranted", frozenset({"fleet:create"}), "oauth", kind="owner",
                       subject="owner:vincent")
    with pytest.raises(FleetRefusal) as caught:
        h.service.create_session(denied, device=TOKYO, project="outside-grant")
    assert caught.value.code == "APPROVAL_REQUIRED"


@pytest.mark.parametrize("field,value", [("kind", "owner"), ("subject", "owner:vincent"),
                                        ("principal", "local-owner")])
def test_tool_arguments_cannot_forge_owner_identity(tmp_path, field, value):
    h = Harness(tmp_path)
    result = call_tool(h.context(), "fleet_create_session", {
        "device": TOKYO, "project": "p-1", field: value})
    assert result["isError"] is True
    assert h.providers[TOKYO].created == []


@pytest.mark.parametrize("entry", [
    {"*": {"subject": "owner:1", "kind": "owner"}},
    {"oauth:client": {"subject": "owner:1", "kind": "administrator"}},
    {"oauth:client": {"subject": "owner:1", "kind": "owner", "scopes": ["fleet:send"]}},
    {"oauth:client": {"subject": "secret prompt text", "kind": "agent"}},
    {"oauth:a": {"subject": "owner:1", "kind": "owner"},
     "oauth:b": {"subject": "owner:1", "kind": "client"}},
])
def test_invalid_trusted_mapping_is_refused_at_config_load(tmp_path, entry):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"schema": "serveros/fleet-mcp.local/v1", "device_ref": "device:work-fj", "auth": {"principals": entry}}))
    with pytest.raises((ValueError, FleetRefusal)):
        load_config(path)


def test_subject_survives_real_token_refresh_and_claims_cannot_promote_client(tmp_path):
    h = Harness(tmp_path)
    oauth = build_oauth(h.config, base_url="http://127.0.0.1:3401", store_root=tmp_path / "oauth")
    client = oauth.store.register_client(client_name="fixture", redirect_uris=["https://example.com/cb"])
    name = "oauth:" + client.client_id
    config = mapped_config(h, **{name: {"subject": "owner:vincent", "kind": "owner"}})
    surface = HttpSurface(service=h.service, config=config, oauth=oauth)
    refresh = oauth.store.mint_refresh_token(client_id=client.client_id, subject="forged-owner",
                                             scopes=("fleet:read",), resource=oauth.resource_url)
    first = oauth.token({"grant_type": "refresh_token", "refresh_token": refresh,
                         "client_id": client.client_id})
    second = oauth.token({"grant_type": "refresh_token", "refresh_token": first["refresh_token"],
                          "client_id": client.client_id})
    before, after = (surface.principal_for_token(row["access_token"]) for row in (first, second))
    assert (before.subject, after.subject, before.name, after.name) == (
        "owner:vincent", "owner:vincent", name, name)
    unmapped = HttpSurface(service=h.service, config=h.config, oauth=oauth).principal_for_token(
        second["access_token"])
    assert (unmapped.kind, unmapped.subject) == ("client", name)
    assert after.scopes == frozenset({"fleet:read"})
    oauth.store.revoke_client(client.client_id)
    # A new request must authenticate again before either inbox read or keyed replay.
    for tool, args in (("fleet_inbox", {}), ("fleet_create_session", {
            "device": TOKYO, "project": "p-1", "idempotency_key": "old"})):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": tool, "arguments": args}}).encode()
        assert surface.handle("POST", "/mcp", "", {
            "authorization": "Bearer " + second["access_token"]}, body)[0] == 401


def test_aliases_keep_independent_inbox_cursors(tmp_path):
    h = Harness(tmp_path)
    events = store(tmp_path / "events.db")
    events.append(event())
    s = FleetService(h.config, providers=h.providers, events=events)
    a = Principal("alias-a", frozenset({"fleet:read"}), "oauth", "owner", "owner:1")
    b = Principal("alias-b", frozenset({"fleet:read"}), "oauth", "owner", "owner:1")
    assert s.inbox(a, ack=True)["acknowledged_cursor"] == 1
    assert s.inbox(a)["events"] == []
    assert len(s.inbox(b)["events"]) == 1


def test_context_changes_conflict_and_plain_calls_keep_the_legacy_digest(tmp_path):
    h = Harness(tmp_path)
    args = {"device": TOKYO, "project": "p-1", "message": "PRIVATE_PROMPT", "idempotency_key": "key"}
    h.service.create_session(h.principal(), **args, **REFS)
    for field in REFS:
        with pytest.raises(FleetRefusal) as caught:
            h.service.create_session(h.principal(), **args, **{**REFS, field: "different:ref"})
        assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(FleetRefusal) as caught:
        h.service.create_session(h.principal(), **args)
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    assert h.service.create_session(h.principal(), **args, **REFS)["idempotent_replay"] is True
    assert len(h.providers[TOKYO].created) == 1
    h.service.create_session(h.principal(), **{**args, "idempotency_key": "legacy"})
    plain = next(r for r in h.service.dispatch._records.values() if "work_ref" not in r)
    legacy = {"principal": '["pat","tester"]', "verb": "create",
        "target": TOKYO + "\u0000claude\u0000p-1", "params": {
            "project": "p-1", "message": "PRIVATE_PROMPT", "provider": "claude", "model": None,
            "permission_mode": None, "lifecycle_mode": None}}
    expected = hashlib.sha256(json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert plain["intent_digest"] == expected
    assert "PRIVATE_PROMPT" not in json.dumps(h.service.dispatch._records)


@pytest.mark.parametrize("value", ["", " ", "raw prompt text", "x" * 513, "ref\nsecret", 42, {}, False])
def test_invalid_context_refuses_before_provider_io(tmp_path, value):
    h = Harness(tmp_path)
    with pytest.raises(FleetRefusal) as caught:
        h.service.create_session(h.principal(), device=TOKYO, project="p-1", work_ref=value)
    assert caught.value.code == "BAD_REQUEST"
    assert h.providers[TOKYO].created == []


@pytest.mark.skipif(os.name == "nt", reason="existing owner requires POSIX file locks")
def test_context_persists_in_existing_owner_and_terminal_event_without_prompt(tmp_path):
    h = Harness(tmp_path)
    owner = owner_store(tmp_path / "owner.json")
    events = store(tmp_path / "events.db")
    s = FleetService(h.config, providers=h.providers, attempts=owner, events=events)
    p = Principal("client-a", h.principal().scopes, "oauth", "agent", "agent:stable")
    s.create_session(p, device=TOKYO, project="p-1", message="PRIVATE_PROMPT",
                     idempotency_key="durable", **REFS)
    row = next(iter(owner.dispatch_attempts().values()))
    assert {k: row[k] for k in REFS} == REFS
    assert (row["principal_subject"], row["principal_kind"], row["project"]) == (
        "agent:stable", "agent", "p-1")
    restarted = owner_store(tmp_path / "owner.json")
    RunWatcher(s, events, restarted).tick()
    result = events.read("fixture", since=0, limit=100, visible=lambda _: True)["events"][0]
    assert {k: result[k] for k in REFS} == REFS
    assert (result["dispatcher"], result["principal_subject"], result["principal_kind"], result["project"]) == (
        '["oauth","client-a"]', "agent:stable", "agent", "p-1")
    assert "PRIVATE_PROMPT" not in (tmp_path / "owner.json").read_text()


def test_tools_carry_context_to_audit_and_output_and_never_provider_arguments(tmp_path):
    h = Harness(tmp_path)
    result = call_tool(h.context(), "fleet_create_session", {
        "device": TOKYO, "project": "p-1", "message": "PRIVATE_PROMPT", "idempotency_key": "tool", **REFS})
    assert not result.get("isError")
    data = result["structuredContent"]
    assert {k: data["created"][k] for k in REFS} == REFS
    assert set(h.providers[TOKYO].created[0]).isdisjoint(REFS)
    rows = [json.loads(line) for line in h.config.audit.path.read_text().splitlines()]
    audit = next(r for r in rows if r["event"] == "fleet.create_session")
    assert {k: audit[k] for k in REFS} == REFS
    assert "PRIVATE_PROMPT" not in json.dumps(rows)


def test_send_context_reaches_event_and_replay_keeps_original_identity_without_io(tmp_path):
    key = SessionKey.compose(TOKYO, "claude", "one")
    h = SendHarness(tmp_path, send_allow=(key.ref,), allow_tokyo_send=True)
    original = Principal("tester", h.principal().scopes, "pat", "agent", "agent:old")
    s = h.service
    result = call_tool(ToolContext(s, original), "fleet_send_to_session", {
        "session_ref": key.ref, "message": "PRIVATE_PROMPT", "idempotency_key": "send", **REFS})
    assert not result["isError"]
    assert {k: result["structuredContent"]["run"][k] for k in REFS} == REFS
    with pytest.raises(FleetRefusal) as conflict:
        s.send_to_session(original, key, message="PRIVATE_PROMPT", idempotency_key="send", **{**REFS, "parent_ref": "changed"})
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    class Attempts:
        def dispatch_attempts(self):
            return s.dispatch._records
    events = store(tmp_path / "events.db")
    RunWatcher(s, events, Attempts()).tick()
    terminal = events.read("fixture", since=0, limit=100, visible=lambda _: True)["events"][0]
    assert {k: terminal[k] for k in REFS} == REFS
    assert (terminal["principal_subject"], terminal["principal_kind"]) == ("agent:old", "agent")
    # A current mapping change cannot rewrite an already accepted dispatch.
    changed = Principal("tester", original.scopes, "pat", "owner", "owner:new")
    def no_io(*args, **kwargs):
        raise AssertionError("accepted replay must stay offline")
    h.tokyo.send = h.tokyo.get_session = no_io
    replay = s.send_to_session(changed, key, message="PRIVATE_PROMPT", idempotency_key="send", **REFS)
    assert replay["idempotent_replay"] is True
    assert (replay["run"]["principal_subject"], replay["run"]["principal_kind"]) == ("agent:old", "agent")
    assert len(h.tokyo.sent) == 1
    # Existing binding revocation is still checked before the saved receipt.
    s.config = replace(h.config, providers={k: replace(v, enabled=False) for k, v in h.config.providers.items()})
    with pytest.raises(FleetRefusal):
        s.send_to_session(changed, key, message="PRIVATE_PROMPT", idempotency_key="send", **REFS)


def test_replay_rejects_corrupt_optional_context_before_disclosure(tmp_path):
    h = Harness(tmp_path)
    h.service.create_session(h.principal(), device=TOKYO, project="p-1", idempotency_key="key", **REFS)
    record = next(iter(h.service.dispatch._records.values()))
    record["work_ref"] = "PRIVATE PROMPT TEXT"
    with pytest.raises(FleetRefusal) as caught:
        h.service.create_session(h.principal(), device=TOKYO, project="p-1", idempotency_key="key", **REFS)
    assert caught.value.code == "DISPATCH_STATE_UNAVAILABLE"


def test_global_read_is_not_restricted_by_create_grants_or_caller_context(tmp_path):
    h = Harness(tmp_path, projects={})
    events = store(tmp_path / "events.db")
    events.append(event(project="unlisted-project", work_ref="work:other"))
    service = FleetService(h.config, providers=h.providers, events=events)
    assert len(service.inbox(h.principal())["events"]) == 1


def test_create_intent_project_is_the_authorized_selected_project(tmp_path):
    h = Harness(tmp_path)
    create = h.providers[TOKYO].create
    def observe_intent(**kwargs):
        pending = next(iter(h.service.dispatch._records.values()))
        assert pending["project"] == "p-1"
        return create(**kwargs)
    h.providers[TOKYO].create = observe_intent
    h.service.create_session(h.principal(), device=TOKYO, project="p-1", message="task", idempotency_key="project")
    row = next(iter(h.service.dispatch._records.values()))
    assert row["project"] == "p-1"


def test_send_records_only_already_verified_project_and_replay_performs_no_read(tmp_path):
    key = SessionKey.compose(TOKYO, "claude", "one")
    h = SendHarness(tmp_path, send_allow=(key.ref,), allow_tokyo_send=True)
    config = replace(h.config, providers={**h.config.providers,
        TOKYO: replace(h.config.providers[TOKYO], multiplex_native_families=True)})
    service = FleetService(config, providers=h.service.providers)
    calls = []
    def get_session(key):
        calls.append(key.ref)
        return {"session": {"id": "one", "provider": "claude", "projectId": "provider-project"}}
    h.tokyo.get_session = get_session
    result = service.send_to_session(h.principal(), key, message="task", idempotency_key="metadata", **REFS)
    assert result["run"]["project"] == "provider-project"
    assert next(iter(service.dispatch._records.values()))["project"] == "provider-project"
    assert service.send_to_session(h.principal(), key, message="task", idempotency_key="metadata", **REFS)["idempotent_replay"]
    assert calls == [key.ref]


def test_revoked_oauth_cannot_read_or_replay_an_existing_accepted_send(tmp_path):
    key = SessionKey.compose(TOKYO, "claude", "one")
    h = SendHarness(tmp_path, send_allow=(key.ref,), allow_tokyo_send=True)
    oauth = build_oauth(h.config, base_url="http://127.0.0.1:3401", store_root=tmp_path / "oauth")
    client = oauth.store.register_client(client_name="fixture", redirect_uris=["https://example.com/cb"])
    token, _ = oauth.store.mint_access_token(client_id=client.client_id, subject="operator",
        scopes=("fleet:read", "fleet:send"), audience=oauth.resource_url)
    surface = HttpSurface(service=h.service, config=h.config, oauth=oauth)
    def invoke(tool, args):
        return surface.handle("POST", "/mcp", "", {"authorization": "Bearer " + token},
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": tool, "arguments": args}}).encode())
    args = {"session_ref": key.ref, "message": "task", "idempotency_key": "accepted", **REFS}
    assert json.loads(invoke("fleet_send_to_session", args)[2])["result"]["isError"] is False
    assert json.loads(invoke("fleet_send_to_session", args)[2])["result"]["structuredContent"]["idempotent_replay"]
    oauth.store.revoke_client(client.client_id)
    assert invoke("fleet_send_to_session", args)[0] == 401
    assert invoke("fleet_inbox", {})[0] == 401
    assert len(h.tokyo.sent) == 1


def test_legacy_receipt_without_metadata_replays_without_relabeling(tmp_path):
    h = Harness(tmp_path)
    h.service.create_session(h.principal(), device=TOKYO, project="p-1", idempotency_key="legacy")
    row = next(iter(h.service.dispatch._records.values()))
    for name in ("principal_subject", "principal_kind", "project"):
        row.pop(name)
    mapped = Principal("tester", h.principal().scopes, "pat", "owner", "owner:new")
    result = h.service.create_session(mapped, device=TOKYO, project="p-1", idempotency_key="legacy")
    assert result["idempotent_replay"] is True
    assert not {"principal_subject", "principal_kind", "project"} & result["created"].keys()


def test_guard_refuses_ack_project_that_differs_from_claim(tmp_path):
    from fleet_mcp.dispatch import DispatchGuard
    h = Harness(tmp_path)
    guard = DispatchGuard()
    key = SessionKey.compose(TOKYO, "claude", "one")
    claim = guard.begin(h.principal(), "create", "target", "key", device_ref=TOKYO,
                        runtime="claude", params={}, project="p-1")
    with pytest.raises(FleetRefusal) as caught:
        guard.complete(claim, {"session": key.as_dict(), "accepted": True, "project": "other"})
    assert caught.value.code == "DISPATCH_OUTCOME_UNKNOWN"
    assert next(iter(guard._records.values()))["phase"] == "EFFECT_UNKNOWN"


@pytest.mark.skipif(os.name == "nt", reason="existing owner requires POSIX file locks")
@pytest.mark.parametrize("metadata", [
    {"principal_kind": "administrator", "principal_subject": "owner:1"},
    {"principal_kind": "owner"}, {"work_ref": "PRIVATE PROMPT TEXT"},
    {"work_ref": "work:1", "unknown_context": "bad"},
])
def test_owner_refuses_malformed_or_open_context_fields(tmp_path, metadata):
    owner = owner_store(tmp_path / "owner.json")
    intent = {"key": "fleet-mcp:test", "action": "fleet.send", "device_ref": TOKYO,
              "verb": "session.send", "intent_digest": "0" * 64, "principal": "actor",
              "runtime": "claude", **metadata}
    with pytest.raises(RuntimeError, match="invalid"):
        owner.claim_dispatch_attempt(intent, actor="actor")
    assert owner.dispatch_attempts() == {}
