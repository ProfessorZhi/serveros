"""One setup produces coherent owner profiles without rewriting other accounts."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from fleet_mcp.config import load_config
from fleet_mcp.scopes import ALLOWED_SCOPES
from test_fleet_create_session import TOKYO

ROOT = Path(__file__).resolve().parents[1]


def setup_inputs(tmp_path):
    source = tmp_path / "fleet.yaml"
    source.write_text(yaml.safe_dump({
        "schema": "serveros/fleet-mcp.local/v1", "device_ref": TOKYO,
        "auth": {"oauth_enabled": False, "grantable_scopes": ["fleet:read"]},
        "providers": {TOKYO: {"kind": "cloudcli-mcp-http", "endpoint": "http://127.0.0.1:1/mcp",
                               "credential_anchor": str(tmp_path / "owner.anchor")}},
        "policy": {"operator_grants": {"other": {TOKYO: ["send"]}}},
    }))
    request = {"owner_max": {"local-owner": [TOKYO, TOKYO + "|codex"]},
               "profiles": {TOKYO: {"owner_approved": True,
                                      "project_roots": {"work": str(tmp_path)}}},
               "multiplex_native_families": [TOKYO]}
    return source, request


def test_setup_is_once_per_owner_and_preserves_other_grants(tmp_path):
    from fleet_mcp.owner_setup import prepare
    source, request = setup_inputs(tmp_path)
    before = source.read_bytes()
    result = prepare(source, request, tmp_path / "prepared")
    candidate = load_config(result["fleet_config"])
    assert source.read_bytes() == before
    assert candidate.policy.owner_max["local-owner"] == (TOKYO, TOKYO + "|codex")
    assert candidate.policy.operator_grants == {"other": {TOKYO: ("send",)}}
    assert candidate.providers[TOKYO].allow_send is False
    assert candidate.providers[TOKYO].allow_create is False
    assert candidate.providers[TOKYO].owner_access.permission_mode == "bypassPermissions"
    assert candidate.auth.grantable_scopes == ("fleet:read",)
    assert result["expected"]["issuer_grantable_scopes"] == ["fleet:read"]
    assert set(result["expected"]["required_owner_scopes"]) == set(ALLOWED_SCOPES)
    assert candidate.policy.create_allow == {}
    assert Path(result["rollback_fleet_config"]).read_bytes() == before


def test_setup_rejects_unbound_namespaces_before_creating_output(tmp_path):
    from fleet_mcp.owner_setup import prepare
    source, request = setup_inputs(tmp_path)
    request["owner_max"]["local-owner"].append(TOKYO + "|claude-unbound")
    with pytest.raises(ValueError):
        prepare(source, request, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()


def test_setup_refuses_credentials_and_duplicate_yaml_keys(tmp_path):
    from fleet_mcp.owner_setup import prepare
    source, request = setup_inputs(tmp_path)
    source.write_text(source.read_text() + "private_key: not-a-real-private-key\n")
    with pytest.raises(ValueError):
        prepare(source, request, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()
    source, request = setup_inputs(tmp_path)
    source.write_text(source.read_text() + "auth: {}\n")
    with pytest.raises(ValueError):
        prepare(source, request, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()


def test_setup_does_not_follow_output_ancestor_symlinks(tmp_path):
    from fleet_mcp.owner_setup import prepare
    source, request = setup_inputs(tmp_path)
    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / "alias").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        prepare(source, request, tmp_path / "alias/prepared")
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("field", ["clientSecret", "apiKey", "privateKey", "passphrase", "secretKey"])
def test_setup_rejects_credential_field_spellings_before_copying(tmp_path, field):
    from fleet_mcp.owner_setup import prepare
    source, request = setup_inputs(tmp_path)
    document = yaml.safe_load(source.read_text())
    document["auth"][field] = "synthetic-canary-credential"
    source.write_text(yaml.safe_dump(document))
    with pytest.raises(ValueError):
        prepare(source, request, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()


def test_setup_rejects_endpoint_query_credentials(tmp_path):
    from fleet_mcp.owner_setup import prepare
    source, request = setup_inputs(tmp_path)
    document = yaml.safe_load(source.read_text())
    document["providers"][TOKYO]["endpoint"] += "?key=synthetic-canary-credential"
    source.write_text(yaml.safe_dump(document))
    with pytest.raises(ValueError):
        prepare(source, request, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()


def test_setup_breaks_yaml_provider_aliases_before_editing(tmp_path):
    from fleet_mcp.owner_setup import prepare
    source, request = setup_inputs(tmp_path)
    document = yaml.safe_load(source.read_text())
    document["providers"]["device:work-fj"] = document["providers"][TOKYO]
    source.write_text(yaml.safe_dump(document))
    result = prepare(source, request, tmp_path / "prepared")
    candidate = load_config(result["fleet_config"])
    assert candidate.providers["device:work-fj"].owner_access is None
    assert candidate.providers["device:work-fj"].allow_create is False


def test_setup_preserves_legacy_explicit_base_runtime_key(tmp_path):
    from fleet_mcp.owner_setup import prepare
    source, request = setup_inputs(tmp_path)
    document = yaml.safe_load(source.read_text())
    document["providers"][TOKYO + "|claude"] = document["providers"].pop(TOKYO)
    source.write_text(yaml.safe_dump(document))
    result = prepare(source, request, tmp_path / "prepared")
    assert load_config(result["fleet_config"]).providers[TOKYO].owner_access is not None


def test_setup_also_emits_local_node_owner_profile(tmp_path):
    from fleet_mcp.owner_setup import prepare
    from fleet_mcp.node.connector import SCHEMA, load_node_config
    source, request = setup_inputs(tmp_path)
    node = tmp_path / "node.yaml"
    node.write_text(yaml.safe_dump({"schema": SCHEMA, "device_ref": TOKYO,
        "secret_anchor": str(tmp_path / "node.anchor"),
        "cloudcli_endpoint": "http://127.0.0.1:1/mcp",
        "cloudcli_credential_anchor": str(tmp_path / "provider.anchor")}))
    original = node.read_bytes()
    result = prepare(source, request, tmp_path / "prepared", node_sources={TOKYO: node})
    candidate = load_node_config(result["node_configs"][TOKYO])
    assert candidate.owner_access is not None and candidate.allow_create is True
    assert candidate.allow_approve is True and candidate.allow_navigate is True
    assert candidate.allow_all_sessions_for_owner == ("send", "control")
    assert node.read_bytes() == original


def test_full_built_runtime_starts_owned_entry_outside_checkout(tmp_path):
    from test_build import build, make_ability_repo, write_pack_contract
    from test_fleet_owned_runtime import existing_store
    source, _ = setup_inputs(tmp_path)
    ability, rev = make_ability_repo(tmp_path / "catalogue")
    pack = write_pack_contract(tmp_path)
    output = tmp_path / "runtime"
    code = build.build(ROOT / "plugin/bundle.yaml", ability, rev, output,
                       tmp_path / "marketplace.json", True, pack)
    assert code == 0
    assert (output / "fleet_mcp/fleet.py").is_file()
    assert (output / "runtime/fleet_mcp_owner.py").is_file()
    state = tmp_path / "state"
    existing_store(state / "runtime/agent-control/store.json")
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "artifact-acceptance", "version": "1"}}}
    import os
    env = dict(os.environ, SERVEROS_STATE_DIR=str(state), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-I", str(output / "bin/serveros-fleet-owned"),
                             "--stdio", "--config", str(source)],
                            input="\n".join(json.dumps(row) for row in (request,
                                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})) + "\n",
                            cwd=tmp_path,
                            capture_output=True, text=True, env=env, timeout=20)
    assert result.returncode == 0, result.stderr
    responses = {row["id"]: row for row in map(json.loads, result.stdout.splitlines())}
    assert responses[1]["result"]["serverInfo"]["name"] == "serveros-fleet-mcp"
    names = {row["name"] for row in responses[2]["result"]["tools"]}
    assert len(names) == 34 and "fleet_create_project" in names
