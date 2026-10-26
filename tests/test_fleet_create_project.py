"""New projects are a real, bounded effect, not a caller-provided directory."""
from dataclasses import replace

import pytest

from fleet_mcp import tools
from fleet_mcp.errors import FleetRefusal
from fleet_mcp.fleet import Principal
from fleet_mcp.runtime import owner_principal
from test_fleet_owner_max import owned
from test_fleet_create_session import TOKYO


def project_service(tmp_path):
    h = owned(tmp_path)
    p = h.providers[TOKYO]
    p.project_calls = []

    def create_project(**kw):
        p.project_calls.append(kw)
        return {"projectId": "just-created", "path": kw["project_path"],
                "displayName": kw.get("display_name") or kw["name"],
                "freshDirectoryCreated": True, "outcome": "created"}

    p.create_project = create_project
    return h


def create(h, **kwargs):
    return h.service.create_project(owner_principal(h.config), device=TOKYO,
                                    root="work", name="alpha", idempotency_key="new-project", **kwargs)


def test_create_project_then_maximum_session_without_grant_edits(tmp_path):
    h = project_service(tmp_path)
    result = create(h)
    assert result["project_id"] == "just-created"
    assert result["project_path"] == str(tmp_path / "alpha")
    session = h.service.create_session(owner_principal(h.config), device=TOKYO,
                                       project=result["project_id"], provider="codex")
    assert session["provider"] == "codex"
    assert h.providers[TOKYO].created[0]["permission_mode"] == "bypassPermissions"
    assert h.config.policy.create_allow == {}


def test_project_receipt_replays_offline_and_changed_name_conflicts(tmp_path):
    h = project_service(tmp_path)
    first = create(h)
    (tmp_path / "alpha").mkdir()
    h.providers[TOKYO].create_project = lambda **kw: pytest.fail("offline accepted replay made IO")
    assert create(h)["project_id"] == first["project_id"]
    with pytest.raises(FleetRefusal) as caught:
        h.service.create_project(owner_principal(h.config), device=TOKYO, root="work",
                                 name="beta", idempotency_key="new-project")
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("name", ["../outside", "a/b", "C:\\outside", "NUL", "name."])
def test_project_path_cannot_be_chosen_by_the_caller(tmp_path, name):
    h = project_service(tmp_path)
    with pytest.raises(FleetRefusal):
        h.service.create_project(owner_principal(h.config), device=TOKYO,
                                 root="work", name=name)
    assert h.providers[TOKYO].project_calls == []


def test_project_creation_requires_selected_owner_and_create_scope(tmp_path):
    h = project_service(tmp_path)
    for principal in (Principal("other", owner_principal(h.config).scopes, "oauth"),
                      Principal("local-owner", frozenset(("fleet:read",)), "pat")):
        with pytest.raises(FleetRefusal):
            h.service.create_project(principal, device=TOKYO, root="work", name="alpha")
    assert h.providers[TOKYO].project_calls == []


def test_project_scope_revocation_precedes_offline_replay(tmp_path):
    h = project_service(tmp_path)
    create(h)
    h.service.config = replace(h.config, policy=replace(h.config.policy, owner_max={}))
    with pytest.raises(FleetRefusal):
        create(h)
    assert len(h.providers[TOKYO].project_calls) == 1


def _grant_admitted(tmp_path, verbs=("create",), *, allow_create=False):
    """The standalone HTTP surface: no owner_max, roots authored, grant named."""
    h = project_service(tmp_path)
    binding = replace(h.config.providers[TOKYO], allow_create=allow_create)
    config = replace(h.config, providers={TOKYO: binding}, policy=replace(
        h.config.policy, owner_max={}, operator_grants={"*": {TOKYO: list(verbs)}}))
    h.config = config
    h.service.config = config
    return h


def test_a_create_grant_on_owner_roots_mints_without_the_owner_profile(tmp_path):
    """`owner_max` needs a durable ledger the HTTP surface does not have."""
    h = _grant_admitted(tmp_path)
    principal = Principal("oauth:any-client", owner_principal(h.config).scopes, "oauth")
    result = h.service.create_project(principal, device=TOKYO, root="work",
                                      name="alpha", idempotency_key="granted")
    assert result["admitted_by"] == "operator-grant"
    assert result["project_path"] == str(tmp_path / "alpha")
    assert result.get("fresh_directory_created") is True


@pytest.mark.parametrize("verbs, owner_access", [
    (("send", "control"), "present"),      # no create verb
    (("create",), "absent"),               # no authored roots
])
def test_the_grant_path_needs_both_halves(tmp_path, verbs, owner_access):
    h = _grant_admitted(tmp_path, verbs)
    if owner_access == "absent":
        binding = replace(h.config.providers[TOKYO], owner_access=None)
        h.service.config = replace(h.config, providers={TOKYO: binding})
    principal = Principal("oauth:any-client", owner_principal(h.config).scopes, "oauth")
    with pytest.raises(FleetRefusal) as caught:
        h.service.create_project(principal, device=TOKYO, root="work", name="alpha")
    assert caught.value.code == "APPROVAL_REQUIRED"
    assert h.providers[TOKYO].project_calls == []


def test_the_grant_path_does_not_carry_bypass_permissions(tmp_path):
    """Roots, not authority: a session minted under it takes create_allow."""
    from fleet_mcp.config import CreateGrant
    h = _grant_admitted(tmp_path, allow_create=True)
    principal = Principal("oauth:any-client", owner_principal(h.config).scopes, "oauth")
    result = h.service.create_project(principal, device=TOKYO, root="work",
                                      name="alpha", idempotency_key="granted")
    h.service.config = replace(h.config, policy=replace(
        h.config.policy, create_allow={"just-created": CreateGrant()}))
    h.service.create_session(principal, device=TOKYO, project=result["project_id"])
    assert h.providers[TOKYO].created[0]["permission_mode"] != "bypassPermissions"


def test_status_reports_the_grant_roots_without_a_mode(tmp_path):
    h = _grant_admitted(tmp_path)
    principal = Principal("oauth:any-client", owner_principal(h.config).scopes, "oauth")
    assert h.service.status(principal)["owner_access"] == [
        {"device_ref": TOKYO, "runtime": "claude", "root_refs": ["work"],
         "permission_mode": None, "admitted_by": "operator-grant"}]


@pytest.mark.parametrize("reply", [
    {"projectId": "id", "path": "/different", "freshDirectoryCreated": True},
    {"projectId": "id", "freshDirectoryCreated": False},
    {"path": "/no-id", "freshDirectoryCreated": True},
])
def test_malformed_project_ack_is_not_persisted_as_success(tmp_path, reply):
    h = project_service(tmp_path)
    calls = []
    h.providers[TOKYO].create_project = lambda **kw: calls.append(True) or reply
    for _ in range(2):
        with pytest.raises(FleetRefusal):
            create(h)
    assert calls == [True]


def test_project_tool_is_explicit_and_keeps_native_mirror_count(tmp_path):
    h = project_service(tmp_path)
    tool = tools.SERVED_BY_NAME["fleet_create_project"]
    schema = tool.input_schema
    assert set(schema["required"]) == {"device", "root", "name"}
    assert schema["additionalProperties"] is False
    assert "path" not in schema["properties"]
    assert len(tools.SERVED_TOOLS) == 45
    assert len(tools.NATIVE_MIRROR_TOOLS) == 31
    result = tool.handler(tools.ToolContext(h.service, owner_principal(h.config)),
                          {"device": TOKYO, "root": "work", "name": "alpha"})
    assert result["project_id"] == "just-created"


def test_cloudcli_refuses_a_gateway_without_target_freshness_support(tmp_path):
    from fleet_mcp.providers.cloudcli import CloudCliProvider
    h = project_service(tmp_path)
    provider = CloudCliProvider(h.config.providers[TOKYO])

    class OldGateway:
        def tools_list(self):
            return [{"name": "project_create", "inputSchema": {"properties": {"projectPath": {"type": "string"}}}}]

        def tools_call(self, *args):
            pytest.fail("old gateway must not receive a create effect")

    provider._client = lambda: OldGateway()
    with pytest.raises(FleetRefusal) as caught:
        provider.create_project(root="work", name="alpha", project_path=str(tmp_path / "alpha"))
    assert caught.value.code == "CAPABILITY_UNSUPPORTED"


def test_signed_node_project_route_enforces_its_own_root(tmp_path):
    import threading
    from pathlib import Path
    from fleet_mcp.config import ProviderBinding
    from fleet_mcp.node.connector import Connector, serve
    from fleet_mcp.providers.fleet_node import FleetNodeProvider
    from test_fleet_node_owner_floor import _config
    h = project_service(tmp_path)
    profile = h.config.providers[TOKYO].owner_access
    config = replace(_config(tmp_path, owner_access=profile), device_ref=TOKYO)
    connector = Connector(config)
    connector.provider = h.providers[TOKYO]
    original = connector.provider.create_project

    def actual_directory(**kw):
        Path(kw["project_path"]).mkdir()
        return original(**kw)

    connector.provider.create_project = actual_directory
    server = serve(config, connector=connector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        binding = ProviderBinding(TOKYO, "fleet-node",
            f"http://127.0.0.1:{server.server_port}", config.secret_anchor)
        provider = FleetNodeProvider(binding)
        with pytest.raises(FleetRefusal):
            provider.create_project(root="work", name="drift",
                                    project_path=str(tmp_path / "unrequested-location"))
        assert not (tmp_path / "drift").exists()
        assert connector.provider.project_calls == []
        result = provider.create_project(root="work", name="wire", project_path=str(tmp_path / "wire"))
        assert result["freshDirectoryCreated"] is True
        assert (tmp_path / "wire").is_dir()
        with pytest.raises(FleetRefusal):
            provider._request("POST", "/v1/projects", {"root": "work", "name": "evil", "project_path": "/outside"})
        assert len(connector.provider.project_calls) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
