"""Configured owners can use new projects without granting each project again."""
from dataclasses import replace

import pytest

from fleet_mcp.config import PolicyBinding
from fleet_mcp.errors import FleetRefusal
from fleet_mcp.fleet import FleetService, Principal
from fleet_mcp.owner_access import OwnerAccessProfile
from fleet_mcp.runtime import owner_principal
from fleet_mcp.scopes import ALLOWED_SCOPES
from test_fleet_create_session import Harness, TOKYO


def owned(tmp_path, *, namespaces=None):
    h = Harness(tmp_path)
    profile = OwnerAccessProfile(True, {"work": str(tmp_path)})
    binding = replace(h.config.providers[TOKYO], owner_access=profile,
                      allow_create=False, allow_send=False, allow_control=False,
                      multiplex_native_families=True)
    policy = replace(h.config.policy, create_allow={}, owner_max={
        "local-owner": tuple(namespaces or (TOKYO, TOKYO + "|codex"))})
    h.config = replace(h.config, providers={TOKYO: binding}, policy=policy)
    p = h.providers[TOKYO]
    p.native_reads = []

    def native_call(tool, arguments, **kwargs):
        p.native_reads.append((tool, dict(arguments)))
        if tool == "projects_list":
            return {"projects": [{"projectId": "just-created", "path": str(tmp_path)}]}
        raise AssertionError(tool)

    p.native_call = native_call
    h.service = FleetService(h.config, providers={TOKYO: p})
    return h


@pytest.mark.parametrize("family", ["claude", "codex"])
def test_owner_new_project_gets_maximum_mode_without_project_grant(tmp_path, family):
    h = owned(tmp_path)
    result = h.service.create_session(owner_principal(h.config), device=TOKYO,
                                      project="just-created", provider=family,
                                      idempotency_key="whole-task")
    assert h.providers[TOKYO].created[0]["permission_mode"] == "bypassPermissions"
    assert h.providers[TOKYO].created[0]["provider"] == family
    assert result["provider"] == family
    assert result["control"]["send_allowed"] is True
    assert h.config.policy.create_allow == {}


def test_claude_owner_entry_does_not_authorize_codex_creation(tmp_path):
    h = owned(tmp_path, namespaces=(TOKYO,))
    with pytest.raises(FleetRefusal):
        h.service.create_session(owner_principal(h.config), device=TOKYO,
                                 project="just-created", provider="codex")
    assert h.providers[TOKYO].created == []


def test_unselected_principal_does_not_gain_owner_access(tmp_path):
    h = owned(tmp_path)
    other = Principal("other", frozenset(ALLOWED_SCOPES), "oauth")
    with pytest.raises(FleetRefusal):
        h.service.create_session(other, device=TOKYO, project="just-created")
    assert h.providers[TOKYO].native_reads == []
    assert h.providers[TOKYO].created == []


def test_owner_project_must_exist_on_the_selected_provider(tmp_path):
    h = owned(tmp_path)
    with pytest.raises(FleetRefusal):
        h.service.create_session(owner_principal(h.config), device=TOKYO, project="forged")
    assert h.providers[TOKYO].created == []


def test_accepted_owner_create_replays_offline_but_revocation_refuses(tmp_path):
    h = owned(tmp_path)
    principal = owner_principal(h.config)
    first = h.service.create_session(principal, device=TOKYO, project="just-created",
                                     idempotency_key="whole-task")

    def offline(*args, **kwargs):
        raise AssertionError("accepted replay must not contact the provider")

    h.providers[TOKYO].native_call = offline
    h.providers[TOKYO].create = offline
    replay = h.service.create_session(principal, device=TOKYO, project="just-created",
                                      idempotency_key="whole-task")
    assert replay["session_ref"] == first["session_ref"]
    assert replay["idempotent_replay"] is True
    h.service.config = replace(h.config, policy=replace(h.config.policy, owner_max={}))
    with pytest.raises(FleetRefusal):
        h.service.create_session(principal, device=TOKYO, project="just-created",
                                 idempotency_key="whole-task")


def test_local_scope_expansion_is_opt_in(tmp_path):
    h = owned(tmp_path)
    assert set(owner_principal(h.config).scopes) == set(ALLOWED_SCOPES)
    assert "fleet:control" not in owner_principal().scopes


def test_http_owner_scope_expansion_requires_its_own_entry(tmp_path):
    from fleet_mcp.http_transport import HttpSurface
    h = owned(tmp_path)
    surface = HttpSurface(service=h.service, config=h.config)
    assert "fleet:control" not in surface.principal_for_token("owner-token-value").scopes
    config = replace(h.config, policy=replace(h.config.policy, owner_max={
        **h.config.policy.owner_max, "owner-pat": (TOKYO,)}))
    surface = HttpSurface(service=h.service, config=config)
    assert set(surface.principal_for_token("owner-token-value").scopes) == set(ALLOWED_SCOPES)


def test_a_wildcard_principal_is_admitted_when_none_is_named(tmp_path):
    """`oauth:<client_id>` is an opaque id; the owner should not have to author it."""
    h = owned(tmp_path, namespaces=(TOKYO,))
    h.config = replace(h.config, policy=replace(h.config.policy,
                                                owner_max={"*": (TOKYO,)}))
    h.service.config = h.config
    opaque = Principal("oauth:fleet-some-new-client", frozenset(ALLOWED_SCOPES), "oauth")
    h.service.create_session(opaque, device=TOKYO, project="just-created")
    assert h.providers[TOKYO].created[0]["permission_mode"] == "bypassPermissions"


def test_status_reports_the_roots_the_wildcard_admits(tmp_path):
    """`fleet_create_project` says to read `fleet_status.owner_access` for `root`."""
    h = owned(tmp_path, namespaces=(TOKYO,))
    h.config = replace(h.config, policy=replace(h.config.policy,
                                                owner_max={"*": (TOKYO,)}))
    h.service.config = h.config
    opaque = Principal("oauth:fleet-some-new-client", frozenset(ALLOWED_SCOPES), "oauth")
    reported = h.service.status(opaque)["owner_access"]
    assert reported == [{"device_ref": TOKYO, "runtime": "claude",
                         "root_refs": ["work"],
                         "permission_mode": "bypassPermissions",
                         "admitted_by": "owner-max"}]


def test_an_exact_entry_shadows_the_wildcard(tmp_path):
    """A named principal's own entry decides, including when it is narrower."""
    h = owned(tmp_path, namespaces=(TOKYO,))
    h.config = replace(h.config, policy=replace(
        h.config.policy,
        owner_max={"local-owner": (TOKYO + "|codex",), "*": (TOKYO,)},
    ))
    h.service.config = h.config
    with pytest.raises(FleetRefusal):
        h.service.create_session(owner_principal(h.config), device=TOKYO,
                                 project="just-created", provider="claude")
    assert h.providers[TOKYO].created == []


def test_the_wildcard_does_not_make_a_personal_device_eligible(tmp_path):
    """Ownership eligibility, not the wildcard, keeps a personal device inert.

    Eligibility is `policy.grant_eligible_ownership`; this binding narrows it to
    `company`, which is the only thing that makes the personal owner class inert
    here — the default admits it.
    """
    h = owned(tmp_path, namespaces=(TOKYO,))
    h.config = replace(h.config, policy=replace(
        h.config.policy, owner_max={"*": (TOKYO,)},
        grant_eligible_ownership=("company",)))
    h.service.config = h.config
    h.service._device_ownership = lambda device_ref: "personal"
    opaque = Principal("oauth:fleet-some-new-client", frozenset(ALLOWED_SCOPES), "oauth")
    with pytest.raises(FleetRefusal):
        h.service.create_session(opaque, device=TOKYO, project="just-created")
    assert h.providers[TOKYO].created == []


def test_only_a_bare_star_is_a_wildcard(tmp_path):
    from fleet_mcp.config import load_owner_max
    assert load_owner_max({"*": [TOKYO]}) == {"*": (TOKYO,)}
    for bad in ("oauth:*", "a*b", "*:"):
        with pytest.raises(ValueError):
            load_owner_max({bad: [TOKYO]})


def test_unbound_owner_namespace_is_rejected(tmp_path):
    h = owned(tmp_path)
    with pytest.raises(ValueError):
        replace(h.config, policy=replace(h.config.policy, owner_max={
            "local-owner": (TOKYO + "|claude-unbound",)}))


def test_node_owner_profile_admits_new_projects_and_highest_mode(tmp_path):
    from fleet_mcp.node.connector import Connector
    from test_fleet_node_owner_floor import _config, FakeProvider
    config = _config(tmp_path, owner_access=OwnerAccessProfile(True, {"work": str(tmp_path)}))
    node = Connector(config)
    provider = FakeProvider()
    provider.native_call = lambda *a, **kw: {"projects": [{"projectId": "new-id"}]}
    received = []
    provider.create = lambda **kw: received.append(kw) or {"sessionId": "new-session"}
    node.provider = provider
    assert node._create({"project": "new-id", "provider": "codex"})["sessionId"] == "new-session"
    assert received[0]["permission_mode"] == "bypassPermissions"
    assert node.config.allow_create is True and node.config.allow_control is True
    assert set(node.config.allow_all_sessions_for_owner) == {"send", "control"}
    with pytest.raises(FleetRefusal):
        node._create({"project": "not-owned"})
    assert len(received) == 1


@pytest.mark.parametrize("family", ["claude", "codex"])
def test_native_create_uses_same_namespace_and_maximum_default(tmp_path, family):
    h = owned(tmp_path)
    provider = h.providers[TOKYO]
    original = provider.native_call
    calls = []

    def native_call(tool, arguments, **kwargs):
        if tool == "session_create":
            calls.append(dict(arguments))
            return {"sessionId": "native-created"}
        return original(tool, arguments, **kwargs)

    provider.native_call = native_call
    h.service.native_call(owner_principal(h.config), device_ref=TOKYO,
                          native_tool="session_create", project="just-created",
                          arguments={"project": "just-created", "provider": family})
    assert calls[0]["permissionMode"] == "bypassPermissions"


def test_codex_owner_does_not_inherit_claude_model_defaults(tmp_path):
    from fleet_mcp.runtime_create_policy import RuntimeProjectDefaults
    h = owned(tmp_path)
    rule = RuntimeProjectDefaults("cloudcli-deepseek", "claude", "claude-account-model",
                                  "default", ("claude-account-model",), ("default",))
    h.config = replace(h.config, policy=replace(h.config.policy,
        runtime_create_defaults={TOKYO: {"just-created": rule}}))
    h.service.config = h.config
    h.service.create_session(owner_principal(h.config), device=TOKYO,
                              project="just-created", provider="codex")
    assert h.providers[TOKYO].created[0]["model"] is None
    assert h.providers[TOKYO].created[0]["permission_mode"] == "bypassPermissions"


def test_codex_owner_cannot_name_a_claude_global_model_without_family_proof(tmp_path):
    from fleet_mcp.config import CreateGrant
    h = owned(tmp_path)
    h.service.config = replace(h.config, policy=replace(h.config.policy,
        create_allow={"just-created": CreateGrant(models=("claude-account-model",))}))
    with pytest.raises(FleetRefusal):
        h.service.create_session(owner_principal(h.config), device=TOKYO,
                                 project="just-created", provider="codex", model="claude-account-model")
    assert h.providers[TOKYO].created == []
