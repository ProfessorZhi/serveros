"""CloudCLI's model library through Fleet: keys never cross, policy decides.

The owner's wish (2026-11-06): pick a CloudCLI model per task through keys,
flexibly, but no secret ever passes through GPT or an agent.
"""
import io
import json
import os
import urllib.error
from pathlib import Path

import pytest

from fleet_mcp import cloudcli_models as cm
from fleet_mcp.errors import FleetRefusal
from test_fleet_mcp_surface import Harness, TOKYO

GW_ROWS = [
    {"key": "ANTHROPIC_BASE_URL", "kind": "value", "value": "https://gw0.example.invalid"},
    {"key": "ANTHROPIC_AUTH_TOKEN", "kind": "envref", "variable": "GW0_TOKEN"},
    {"key": "ANTHROPIC_MODEL", "kind": "value", "value": "deepseek-chat"},
    {"key": "ANTHROPIC_API_KEY", "kind": "unset"},
]


# -- the rows ------------------------------------------------------------------

def test_a_gateway_entry_with_an_envref_token_is_accepted():
    rows = cm.check_entry("claude", "deepseek-gw0", "DeepSeek via gateway 0", GW_ROWS)
    assert {"key": "ANTHROPIC_AUTH_TOKEN", "kind": "envref", "value": "GW0_TOKEN"} in rows


def test_a_secret_value_is_refused():
    with pytest.raises(FleetRefusal, match="never written through Fleet"):
        cm.check_entry("claude", "x", "x", [{"key": "ANTHROPIC_AUTH_TOKEN", "kind": "secret",
                                             "value": "sk-live"}])


def test_a_credential_as_plain_value_is_refused():
    with pytest.raises(FleetRefusal, match="only be an envref"):
        cm.check_entry("claude", "x", "x", [{"key": "ANTHROPIC_AUTH_TOKEN", "kind": "value",
                                             "value": "sk-live"}])


def test_a_secret_row_without_value_keeps_the_stored_one():
    assert cm.check_entry("claude", "x", "x", [{"key": "ANTHROPIC_AUTH_TOKEN", "kind": "secret"}]) \
        == [{"key": "ANTHROPIC_AUTH_TOKEN", "kind": "secret"}]


def test_source_must_fit_the_rows():
    rows = cm.check_entry("claude", "x", "x", GW_ROWS)
    assert cm.source_contradiction("gateway", rows) is None
    assert "cannot carry a base URL" in cm.source_contradiction("official_subscription", rows)
    assert "needs a base URL" in cm.source_contradiction("gateway", [])


def test_every_model_name_the_entry_runs_is_judged():
    rows = cm.check_entry("claude", "deepseek-gw0", "x", GW_ROWS)
    assert cm.declared_models("deepseek-gw0", rows) == ["deepseek-gw0", "deepseek-chat"]


# -- reading never carries a value ----------------------------------------------

LISTING = {"success": True, "data": {"provider": "claude", "models": {
    "DEFAULT": "sonnet",
    "OPTIONS": [
        {"value": "sonnet", "label": "Sonnet", "isCustom": False},
        {"value": "deepseek-gw0", "label": "DeepSeek gw0", "isCustom": True, "recordId": 7,
         "config": {"env": [
             {"key": "ANTHROPIC_BASE_URL", "kind": "value", "value": "https://gw0"},
             {"key": "ANTHROPIC_AUTH_TOKEN", "kind": "secret", "isSet": True},
             {"key": "OPENAI_API_KEY", "kind": "value", "value": "pasted-by-hand"},
             {"key": "GW_KEY", "kind": "envref", "value": "GW0_TOKEN"},
         ]}},
    ]}}}


def test_the_projection_withholds_every_credential_value():
    out = cm.project("claude", LISTING)
    text = json.dumps(out)
    assert "pasted-by-hand" not in text
    entry = out["entries"][1]
    assert {"key": "ANTHROPIC_AUTH_TOKEN", "kind": "secret", "is_set": True} in entry["env"]
    assert any(r.get("withheld") for r in entry["env"] if r["key"] == "OPENAI_API_KEY")
    assert {"key": "GW_KEY", "kind": "envref", "variable": "GW0_TOKEN"} in entry["env"]


# -- the device side ---------------------------------------------------------------

class _Resp(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(json.dumps(body).encode())
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _token(tmp_path: Path) -> Path:
    path = tmp_path / "token"
    path.write_text("jwt-1")
    os.chmod(path, 0o600)
    return path


def test_upsert_patches_an_existing_custom_entry_and_reads_it_back(tmp_path):
    calls = []
    state = json.loads(json.dumps(LISTING))

    def opener(req, timeout):
        calls.append((req.get_method(), req.full_url, req.headers.get("Authorization")))
        if req.get_method() == "PATCH":
            body = json.loads(req.data)
            state["data"]["models"]["OPTIONS"][1].update(
                {"label": body["model"], "config": body["config"]})
            return _Resp({"success": True}, {"X-Refreshed-Token": "jwt-2"})
        return _Resp(state)

    lib = cm.LocalLibrary("http://127.0.0.1:3001/mcp", _token(tmp_path), opener=opener)
    rows = cm.check_entry("claude", "deepseek-gw0", "DeepSeek gw0", GW_ROWS)
    out = lib.upsert("claude", "deepseek-gw0", "DeepSeek gw0", rows)
    assert out["action"] == "updated" and out["verified"] is True
    assert ("PATCH", "http://127.0.0.1:3001/api/providers/claude/models/7", "Bearer jwt-1") in calls
    assert (tmp_path / "token").read_text() == "jwt-2"      # the rotated token is kept
    assert "pasted" not in json.dumps(out)


def test_a_builtin_model_is_never_shadowed(tmp_path):
    lib = cm.LocalLibrary("http://127.0.0.1:3001/mcp", _token(tmp_path),
                          opener=lambda req, timeout: _Resp(LISTING))
    with pytest.raises(FleetRefusal, match="built-in"):
        lib.upsert("claude", "sonnet", "x", [])


def test_the_library_is_reached_on_loopback_only(tmp_path):
    with pytest.raises(FleetRefusal, match="loopback"):
        cm.LocalLibrary("https://fleet.example.com/mcp", _token(tmp_path))


def test_a_token_readable_by_others_is_refused(tmp_path):
    path = _token(tmp_path)
    os.chmod(path, 0o644)
    with pytest.raises(FleetRefusal, match="0600"):
        cm.read_token(path)


def test_an_expired_token_says_how_to_renew(tmp_path):
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b"{}"))
    lib = cm.LocalLibrary("http://127.0.0.1:3001/mcp", _token(tmp_path), opener=opener)
    with pytest.raises(FleetRefusal, match="logs in again"):
        lib.list("claude")


# -- the hub ---------------------------------------------------------------------

@pytest.fixture
def a6000_like_policy(tmp_path, monkeypatch):
    path = tmp_path / "runtime-policy.yaml"
    path.write_text(
        "schema: serveros/fleet-runtime-policy.v1\n"
        "devices:\n"
        f"  {TOKYO}:\n"
        "    runtimes:\n"
        "      claude-code:\n"
        "        model_sources: [gateway]\n"
        "        gateways: [target:gateway0]\n"
        "        model_patterns: ['^deepseek']\n")
    monkeypatch.setenv("SERVEROS_RUNTIME_POLICY", str(path))


def _upsert(h, **kw):
    args = dict(device=TOKYO, model_id="deepseek-gw0", label="DeepSeek gw0", env=GW_ROWS,
                source="gateway", gateway="target:gateway0")
    args.update(kw)
    return h.service.upsert_model(h.principal("fleet:read", "fleet:control"), **args)


def test_policy_refuses_a_subscription_entry_before_anything_is_written(tmp_path, a6000_like_policy):
    h = Harness(tmp_path)
    with pytest.raises(FleetRefusal) as refused:
        _upsert(h, model_id="sonnet-x", env=[], source="official_subscription", gateway=None)
    assert "runtime policy refuses" in str(refused.value)


def test_policy_refuses_another_gateway(tmp_path, a6000_like_policy):
    h = Harness(tmp_path)
    with pytest.raises(FleetRefusal, match="gateway target:gateway9"):
        _upsert(h, gateway="target:gateway9")


def test_policy_refuses_a_model_the_rows_switch_to(tmp_path, a6000_like_policy):
    h = Harness(tmp_path)
    rows = [r if r["key"] != "ANTHROPIC_MODEL" else {**r, "value": "gpt-5"} for r in GW_ROWS]
    with pytest.raises(FleetRefusal, match="gpt-5"):
        _upsert(h, env=rows)


def test_the_hub_refuses_a_secret_value_too(tmp_path, a6000_like_policy):
    h = Harness(tmp_path)
    with pytest.raises(FleetRefusal, match="never written through Fleet"):
        _upsert(h, env=[{"key": "ANTHROPIC_AUTH_TOKEN", "kind": "secret", "value": "sk"}])


def test_a_write_needs_the_control_scope(tmp_path, a6000_like_policy):
    h = Harness(tmp_path)
    with pytest.raises(FleetRefusal):
        h.service.upsert_model(h.principal("fleet:read"), device=TOKYO, model_id="deepseek-gw0",
                               label="x", env=GW_ROWS, source="gateway", gateway="target:gateway0")


def test_listing_annotates_each_entry_with_the_policy(tmp_path, a6000_like_policy):
    h = Harness(tmp_path)
    h.tokyo.list_models = lambda *, provider: cm.project(provider, LISTING)
    out = h.service.list_models(h.principal("fleet:read"), device=TOKYO)
    by_id = {e["model_id"]: e for e in out["entries"]}
    assert by_id["sonnet"]["policy"]["allowed"] is False
    assert by_id["deepseek-gw0"]["policy"]["inferred_source"] == "gateway"
    assert "pasted-by-hand" not in json.dumps(out)


# -- the commander never has to name a model --------------------------------------

def test_no_model_named_takes_the_policy_default_when_the_device_has_it(tmp_path, monkeypatch):
    from fleet import intelligence
    h = Harness(tmp_path)
    monkeypatch.setattr(intelligence, "default_choice",
                        lambda d, k: {"allowed": True, "model": "deepseek-gw0", "reason": "x"})
    h.tokyo.list_models = lambda *, provider: cm.project(provider, LISTING)
    assert h.service._apply_runtime_policy(TOKYO, "claude", "claude", None, None) == (
        "deepseek-gw0", "runtime policy default_model")


def test_a_default_the_device_lacks_falls_back_to_the_device_default(tmp_path, monkeypatch):
    from fleet import intelligence
    h = Harness(tmp_path)
    monkeypatch.setattr(intelligence, "default_choice",
                        lambda d, k: {"allowed": True, "model": "not-there", "reason": "x"})
    h.tokyo.list_models = lambda *, provider: cm.project(provider, LISTING)
    assert h.service._apply_runtime_policy(TOKYO, "claude", "claude", None, None) == (None, None)


def test_a_runtime_the_policy_does_not_name_is_refused_at_create(tmp_path, monkeypatch):
    from fleet import intelligence
    h = Harness(tmp_path)
    monkeypatch.setattr(intelligence, "default_choice",
                        lambda d, k: {"allowed": False, "model": None,
                                      "reason": "codex is not allowed on x"})
    with pytest.raises(FleetRefusal, match="codex is not allowed"):
        h.service._apply_runtime_policy(TOKYO, "claude", "codex", None, None)


# -- self-tuning inside the owner's bounds ------------------------------------------

def test_tune_sets_reverts_and_journals_the_default(tmp_path, a6000_like_policy, monkeypatch):
    from fleet import intelligence
    path = tmp_path / "runtime-policy.yaml"
    path.write_text(path.read_text() + "tunable:\n  default_model: true\n")
    h = Harness(tmp_path)
    h.tokyo.list_models = lambda *, provider: cm.project(provider, LISTING)
    p = h.principal("fleet:read", "fleet:control")
    out = h.service.tune(p, action="set", device=TOKYO, value="deepseek-gw0",
                         reason="faster for batch work")
    assert out["before"] is None and out["after"] == "deepseek-gw0"
    assert intelligence.default_choice(TOKYO, "claude-code")["model"] == "deepseek-gw0"
    rule = intelligence.policy_rule(TOKYO, "claude-code")
    assert rule["gateways"] == ["target:gateway0"]          # the bounds did not move
    h.service.tune(p, action="revert", device=TOKYO, reason="undo")
    assert intelligence.default_choice(TOKYO, "claude-code")["model"] is None
    assert len(h.service.tune(p, action="history")["journal"]) == 2


def test_tune_refuses_outside_the_bounds_and_unknown_entries(tmp_path, a6000_like_policy):
    path = tmp_path / "runtime-policy.yaml"
    path.write_text(path.read_text() + "tunable:\n  default_model: true\n")
    h = Harness(tmp_path)
    h.tokyo.list_models = lambda *, provider: cm.project(provider, LISTING)
    p = h.principal("fleet:read", "fleet:control")
    with pytest.raises(FleetRefusal, match="outside the owner's bounds"):
        h.service.tune(p, action="set", device=TOKYO, value="gpt-5", reason="x")
    with pytest.raises(FleetRefusal, match="not in"):
        h.service.tune(p, action="set", device=TOKYO, value="deepseek-missing", reason="x")


def test_tune_needs_the_owner_to_make_the_knob_tunable(tmp_path, monkeypatch):
    path = tmp_path / "runtime-policy.yaml"
    path.write_text("tunable:\n  default_model: false\n")
    monkeypatch.setenv("SERVEROS_RUNTIME_POLICY", str(path))
    h = Harness(tmp_path)
    with pytest.raises(FleetRefusal, match="not made default_model tunable"):
        h.service.tune(h.principal("fleet:read", "fleet:control"), action="set",
                       device=TOKYO, value="deepseek-gw0", reason="x")


def test_a_gateway_entry_is_recognised_from_its_base_url(tmp_path, a6000_like_policy):
    path = tmp_path / "runtime-policy.yaml"
    path.write_text(path.read_text() + "gateway_hosts:\n  target:gateway0: ['127.0.0.1:26601']\n")
    from fleet import intelligence
    assert intelligence.gateway_for_url("http://127.0.0.1:26601") == "target:gateway0"
    assert intelligence.gateway_for_url("http://10.0.0.9:4000") is None
    listing = {"data": {"models": {"DEFAULT": "default", "OPTIONS": [
        {"value": "deepseek-x", "label": "gw0", "isCustom": True, "config": {"env": [
            {"key": "ANTHROPIC_BASE_URL", "kind": "value", "value": "http://127.0.0.1:26601"}]}}]}}}
    h = Harness(tmp_path)
    h.tokyo.list_models = lambda *, provider: cm.project(provider, listing)
    entry = h.service.list_models(h.principal("fleet:read"), device=TOKYO)["entries"][0]
    assert entry["policy"]["inferred_gateway"] == "target:gateway0"
    assert entry["policy"]["allowed"] is True


def test_the_model_tools_default_to_the_devices_own_runtime(tmp_path, a6000_like_policy, monkeypatch):
    h = Harness(tmp_path)
    seen = {}
    real = h.service._own_runtime
    monkeypatch.setattr(h.service, "_own_runtime", lambda ref: seen.setdefault("asked", ref) and real(ref))
    h.tokyo.list_models = lambda *, provider: cm.project(provider, LISTING)
    h.service.list_models(h.principal("fleet:read"), device=TOKYO)
    assert seen["asked"] == TOKYO
