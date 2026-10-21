#!/usr/bin/env python3
"""R1 #248 — rejection-path tests for the 2026-10-20 authorization matrix.

Evidence level: SOURCE_READ + REAL_READ_ONLY observation, NOT_RUN lifecycle.
Nothing here calls a provider, opens a socket, reads a credential, starts a
process or makes a model request. The fixture is data; the projector is pure.

The tests pin three separations that are easy to collapse:

1. A source REFUSAL, an unevaluated UNKNOWN and an owner/enrollment BLOCKED
   are different answers. UNKNOWN is never a deny; BLOCKED is never a source
   reason.
2. The source's step order is observable. Codex is refused at the runtime step
   before its provider support is read; Claude Code is refused at the support
   step because BotMux publishes no instance-level table; readiness UNKNOWN is
   unevaluated, not refused.
3. `effective` is accepted only from a named FACT provider field. A requested,
   resolved, configured or default value never becomes effective.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "integrations" / "fleet-v1-runtime"
MODULE_PATH = PKG / "authorization.py"
CONTRACT_PATH = PKG / "contract.yaml"
FIXTURE_PATH = PKG / "fixtures" / "authorization-2026-10-20.json"
OBSERVED_PATH = PKG / "observed" / "tokyo-2026-10-20.json"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "fleet_v1_runtime_authorization", MODULE_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {MODULE_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


authorization = _load_module()
CONTRACT = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
OBSERVED = json.loads(OBSERVED_PATH.read_text(encoding="utf-8"))


def _deep_merge(base, patch):
    """Merge mappings; replace lists and scalars. Lists are closed answers."""
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _case(case):
    observation = _deep_merge(
        FIXTURE["base_observation"], case.get("patch_observation") or {})
    request = _deep_merge(FIXTURE["base_request"], case.get("request") or {})
    return authorization.project(observation, request)


CASES = FIXTURE["cases"]
IDS = [case["name"] for case in CASES]


# ---------------------------------------------------------------------------
# The fixture matrix.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_fixture_case(case):
    out = _case(case)
    expected = case["expect"]

    assert out["schema"] == authorization.SCHEMA
    assert out["evidence_level"] == authorization.EVIDENCE_LEVEL
    assert out["session_owner"] == authorization.SESSION_OWNER
    assert out["authority"] == authorization.AUTHORITY
    assert out["fallback"] is None
    assert out["full_v1_codex"] is False
    assert out["decision"] in authorization.DECISIONS
    assert out["decision"] == expected["decision"]
    assert out["reason"] == expected["reason"]
    assert out["blocked_codes"] == expected["blocked_codes"]

    # A reason is either absent or a word from the source's closed list.
    if out["reason"] is not None:
        assert out["reason"] in authorization.REASONS

    # Every blocked code is an R1 boundary word, never a source reason.
    for code in out["blocked_codes"]:
        assert code in authorization.BLOCKED_CODES
        assert code not in authorization.REASONS

    if "effective" in expected:
        for field, want in expected["effective"].items():
            got = out["effective"][field]
            assert got["value"] == want["value"], field
            assert got["provenance"] == want["provenance"], field
            if "source" in want:
                assert got["source"] == want["source"], field

    if "not_contains" in expected:
        assert expected["not_contains"] not in json.dumps(out, sort_keys=True)


def test_fixture_has_unique_case_names():
    assert len(IDS) == len(set(IDS))


def test_fixture_covers_every_decision_kind():
    decisions = {case["expect"]["decision"] for case in CASES}
    assert decisions == {"PRECONDITIONS_MET", "REFUSED", "UNKNOWN", "BLOCKED"}


def test_every_blocked_code_is_exercised():
    used = {code for case in CASES for code in case["expect"]["blocked_codes"]}
    assert used == set(authorization.BLOCKED_CODES)


def test_every_fixture_blocked_code_is_declared_in_contract():
    declared = set(CONTRACT["blocked_codes"])
    used = {code for case in CASES for code in case["expect"]["blocked_codes"]}
    assert used <= declared


def test_contract_reason_vocabulary_matches_module():
    assert tuple(CONTRACT["reason_vocabulary"]) == authorization.REASONS


def test_contract_blocked_codes_match_module():
    assert set(CONTRACT["blocked_codes"]) == set(authorization.BLOCKED_CODES)


# ---------------------------------------------------------------------------
# The three separations.
# ---------------------------------------------------------------------------

def test_cc_first_read_path_is_partial_not_a_lifecycle():
    direct = next(c for c in CASES if c["name"] == "cc-first-session-metadata-read")
    routed = next(c for c in CASES if c["name"] == "cc-first-routed-inspect-support-unpublished")

    assert _case(direct)["decision"] == "PRECONDITIONS_MET"
    assert _case(direct)["tier"] == "CC_FIRST"
    assert _case(direct)["tier_state"] == "PARTIAL"
    assert _case(routed)["decision"] == "REFUSED"
    assert _case(routed)["reason"] == "ACTION_UNSUPPORTED"


def test_codex_runtime_gate_precedes_provider_support():
    case = next(c for c in CASES if c["name"] == "codex-routed-inspect-runtime-not-declared")
    out = _case(case)
    # The Codex provider declares session.inspect SUPPORTED. It does not
    # matter: the registry does not declare the runtime, and that step runs
    # first.
    assert out["reason"] == "AGENT_RUNTIME_UNAVAILABLE"
    assert out["provider_support"] is None
    assert "BLOCKED_RUNTIME_NOT_DECLARED" in out["blocked_codes"]


def test_botmux_support_unpublished_precedes_readiness():
    case = next(c for c in CASES if c["name"] == "cc-first-routed-inspect-support-unpublished")
    out = _case(case)
    # BotMux's per-session row says session.inspect SUPPORTED. The resolver
    # reads the provider instance table, which is empty.
    assert out["reason"] == "ACTION_UNSUPPORTED"
    assert out["provider_support"] == {"state": "UNKNOWN"}
    assert "BLOCKED_PROVIDER_SUPPORT_UNPUBLISHED" in out["blocked_codes"]


def test_readiness_unknown_is_not_a_refusal():
    case = next(c for c in CASES if c["name"] == "codex-create-hypothetical-readiness-unknown")
    out = _case(case)
    assert out["decision"] == "UNKNOWN"
    assert out["reason"] is None
    assert out["resolution_status"] == "UNKNOWN"
    assert "BLOCKED_EXECUTION_UNVERIFIED" in out["blocked_codes"]


def test_grant_unknown_is_not_a_deny_and_not_an_allow():
    case = next(c for c in CASES if c["name"] == "codex-create-hypothetical-readiness-available-grant-unknown")
    out = _case(case)
    assert out["decision"] == "UNKNOWN"
    assert out["reason"] is None
    assert "BLOCKED_GRANT_UNKNOWN" in out["blocked_codes"]


def test_owner_consent_is_not_a_source_reason():
    case = next(c for c in CASES if c["name"] == "cloudcli-create-blocked-owner-consent")
    out = _case(case)
    assert out["decision"] == "BLOCKED"
    assert out["reason"] is None
    assert out["blocked_codes"] == ["BLOCKED_OWNER_CONSENT"]


def test_owner_login_is_scoped_to_agent_inventory():
    inventory = next(c for c in CASES if c["name"] == "cc-first-agent-inventory-needs-login")
    metadata = next(c for c in CASES if c["name"] == "cc-first-session-metadata-read")
    assert _case(inventory)["blocked_codes"] == ["BLOCKED_OWNER_LOGIN"]
    assert _case(metadata)["decision"] == "PRECONDITIONS_MET"


# ---------------------------------------------------------------------------
# Surface facts.
# ---------------------------------------------------------------------------

def test_serveros_interrupt_tool_is_absent():
    assert CONTRACT["surfaces"]["serveros-agent-mcp"]["tools"]["interrupt"] == []
    assert authorization.SERVEROS_MCP_TOOL["interrupt"] is None
    case = next(c for c in CASES if c["name"] == "serveros-interrupt-tool-absent")
    out = _case(case)
    assert out["reason"] == "ACTION_UNSUPPORTED"
    assert "BLOCKED_MCP_TOOL_ABSENT" in out["blocked_codes"]


def test_cloudcli_has_no_resume_tool():
    assert CONTRACT["surfaces"]["cloudcli-mcp"]["tools"]["resume"] == []
    assert authorization.CLOUDCLI_TOOL["resume"] is None
    case = next(c for c in CASES if c["name"] == "cloudcli-resume-unsupported")
    assert _case(case)["reason"] == "ACTION_UNSUPPORTED"


def test_cloudcli_get_run_protocol_mismatch_is_unknown_not_success():
    case = next(c for c in CASES if c["name"] == "cloudcli-run-get-protocol-mismatch")
    out = _case(case)
    assert out["decision"] == "UNKNOWN"
    assert out["reason"] is None
    assert "BLOCKED_PROTOCOL_FIELD_MISMATCH" in out["blocked_codes"]


def test_claude_native_adapter_is_absent():
    case = next(c for c in CASES if c["name"] == "claude-code-native-adapter-absent")
    out = _case(case)
    assert out["decision"] == "BLOCKED"
    assert out["blocked_codes"] == ["BLOCKED_PROVIDER_ADAPTER_ABSENT"]


# ---------------------------------------------------------------------------
# Mutations require identity, role and binding — in that order.
# ---------------------------------------------------------------------------

def _declared_codex_ready():
    return _deep_merge(FIXTURE["base_observation"], {
        "devices": {"device:tokyo-shared-dev-01": {
            "declared_runtimes": ["agent-runtime:claude-code", "agent-runtime:codex"]}},
        "providers": {"codex": {
            "execution_readiness": "AVAILABLE",
            # The hypothetical stop support lets the human-gated path be
            # exercised; the real base table declares session.stop UNSUPPORTED.
            "instance_action_support": {"session.stop": "SUPPORTED"},
        }},
    })


@pytest.mark.parametrize("verb", ["create", "send", "resume", "end"])
def test_mutating_verbs_require_context_ref(verb):
    request = {
        "evaluated_at": "2026-10-20T05:00:00Z",
        "device_ref": "device:tokyo-shared-dev-01",
        "surface": "serveros-agent-mcp",
        "family": "codex",
        "runtime_tag": "codex",
        "verb": verb,
    }
    out = authorization.project(_declared_codex_ready(), request)
    assert out["decision"] == "REFUSED"
    assert out["reason"] == "INVALID_CONTEXT"
    assert out["requires_context_ref"] is True


def test_create_requires_session_role():
    request = {
        "evaluated_at": "2026-10-20T05:00:00Z",
        "device_ref": "device:tokyo-shared-dev-01",
        "surface": "serveros-agent-mcp",
        "family": "codex",
        "runtime_tag": "codex",
        "verb": "create",
        "context_ref": "ctx-fixture",
        "grant": {"scopes": ["fleet:create"], "allow_create": True},
    }
    out = authorization.project(_declared_codex_ready(), request)
    assert out["reason"] == "INVALID_CONTEXT"
    assert "session_role" in out["detail"]


def test_execution_requires_bound_item():
    request = {
        "evaluated_at": "2026-10-20T05:00:00Z",
        "device_ref": "device:tokyo-shared-dev-01",
        "surface": "serveros-agent-mcp",
        "family": "codex",
        "runtime_tag": "codex",
        "verb": "create",
        "context_ref": "ctx-fixture",
        "session_role": "execution",
        "grant": {"scopes": ["fleet:create"], "allow_create": True},
    }
    out = authorization.project(_declared_codex_ready(), request)
    assert out["reason"] == "INVALID_CONTEXT"
    assert "bound_item_ref" in out["detail"]


def test_send_requires_binding():
    request = {
        "evaluated_at": "2026-10-20T05:00:00Z",
        "device_ref": "device:tokyo-shared-dev-01",
        "surface": "serveros-agent-mcp",
        "family": "codex",
        "runtime_tag": "codex",
        "verb": "send",
        "context_ref": "ctx-fixture",
        "grant": {"scopes": ["fleet:send"], "allow_send": True},
        "binding": "missing",
    }
    out = authorization.project(_declared_codex_ready(), request)
    assert out["reason"] == "INVALID_CONTEXT"
    assert "binding" in out["detail"]


# ---------------------------------------------------------------------------
# Effective fields are never promoted.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provenance,source", [
    ("REQUESTED", None),
    ("RESOLVED", "caller"),
    ("DEFAULT", "default-label"),
    ("CONFIGURED", "provider config"),
    ("INFERRED", "codex:thread/list"),
])
def test_effective_requires_fact_and_named_source(provenance, source):
    observation = _deep_merge(FIXTURE["base_observation"], {
        "providers": {"codex": {
            "effective_model": {"value": "gpt-6-astra",
                                "provenance": provenance,
                                "source": source},
            "effective_permission_mode": {"value": "plan",
                                         "provenance": provenance,
                                         "source": source},
        }},
    })
    request = {
        "evaluated_at": "2026-10-20T05:00:00Z",
        "device_ref": "device:tokyo-shared-dev-01",
        "surface": "codex-app-server",
        "family": "codex",
        "runtime_tag": "codex",
        "verb": "read",
    }
    out = authorization.project(observation, request)
    assert out["effective"]["model"]["value"] is None
    assert out["effective"]["model"]["provenance"] == "UNKNOWN"
    assert out["effective"]["permissionMode"]["value"] is None
    assert out["effective"]["permissionMode"]["provenance"] == "UNKNOWN"


def test_effective_model_and_permission_mode_are_independent():
    observation = _deep_merge(FIXTURE["base_observation"], {
        "providers": {"codex": {
            "effective_model": {"value": "gpt-6-astra",
                                "provenance": "FACT",
                                "source": "codex:thread/list"},
            "effective_permission_mode": {"value": None,
                                         "provenance": "UNKNOWN",
                                         "reason": "not reported"},
        }},
    })
    request = {
        "evaluated_at": "2026-10-20T05:00:00Z",
        "device_ref": "device:tokyo-shared-dev-01",
        "surface": "codex-app-server",
        "family": "codex",
        "runtime_tag": "codex",
        "verb": "read",
    }
    out = authorization.project(observation, request)
    assert out["effective"]["model"]["value"] == "gpt-6-astra"
    assert out["effective"]["model"]["provenance"] == "FACT"
    assert out["effective"]["permissionMode"]["value"] is None
    assert out["effective"]["permissionMode"]["provenance"] == "UNKNOWN"


def test_secret_shaped_runtime_tag_is_refused_without_echo():
    case = next(c for c in CASES
                if c["name"] == "secret-shaped-runtime-tag-refused-without-echo")
    out = _case(case)
    assert out["decision"] == "REFUSED"
    assert out["reason"] == "INVALID_CONTEXT"
    assert "sk-fixture-not-a-real-key" not in json.dumps(out, sort_keys=True)


# ---------------------------------------------------------------------------
# Purity and boundary guards.
# ---------------------------------------------------------------------------

def test_module_has_no_io_or_provider_imports():
    source = MODULE_PATH.read_text(encoding="utf-8")
    forbidden = (
        "import socket", "import subprocess", "import requests",
        "import urllib", "import http", "from mcp", "from fleet",
        "from runtime", "os.environ", "open(",
    )
    for token in forbidden:
        assert token not in source, f"pure module must not contain {token!r}"


def test_module_does_not_read_a_clock():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "datetime.datetime.now" not in source
    assert "datetime.datetime.utcnow" not in source
    assert "time.time" not in source


def test_observed_snapshot_is_redacted_and_not_run():
    text = OBSERVED_PATH.read_text(encoding="utf-8")
    assert OBSERVED["evidence_level"] == "REAL_READ_ONLY"
    assert OBSERVED["pin"]["authoritative_main"] == "771c81c"
    assert "session.create" in OBSERVED["not_run"]
    assert "session.send" in OBSERVED["not_run"]
    assert "session.interrupt" in OBSERVED["not_run"]
    for pattern in (r"sk-[A-Za-z0-9]{8,}", r"gh[pousr]_[A-Za-z0-9]{8,}",
                    r"xox[baprs]-[A-Za-z0-9-]{8,}", r"Bearer\s+\S{8,}"):
        assert not re.search(pattern, text), pattern


def test_authoritative_pin_is_consistent():
    assert CONTRACT["pin"]["authoritative_main"] == "771c81c"
    assert OBSERVED["pin"]["authoritative_main"] == "771c81c"


def test_full_v1_codex_is_not_claimed():
    full = CONTRACT["tiers"]["FULL_V1_CODEX"]
    assert full["observed_state"] == "BLOCKED"
    assert full["blockers"]
    assert _case(next(c for c in CASES
                      if c["name"] == "codex-create-hypothetical-readiness-unknown")
                 )["full_v1_codex"] is False
