#!/usr/bin/env python3
"""R1 #248 — source-level Claude Code / Codex runtime-parity projection.

Evidence level: SOURCE_FIXTURE. Nothing here calls a provider, reads a
credential, opens a socket, starts a process, or claims a live Runtime.

The two invariants this file exists to pin:

1. ``effective`` is never inferred from ``requested``/``resolved``/a default
   label. It is accepted only with provider-response provenance and a named
   source field; otherwise it is ``UNKNOWN``.
2. This fixture is a projection, not a second Session owner. It has no
   create/send/interrupt function, imports no provider module, and says so in
   every record it returns.

Negative coverage is deliberately wider than positive coverage: other OS
principals, an absent installed CLI, a missing grant, a stale session, an
unbound provider, a secret-shaped credential reference, and the runtime-tag /
agent-runtime / family axis confusion.
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
PKG = REPO / "integrations" / "fleet-v1-runtime-parity"
PARITY_PATH = PKG / "parity.py"
CONTRACT_PATH = PKG / "contract.yaml"
POSITIVE_PATH = PKG / "fixtures" / "positive.json"
NEGATIVE_PATH = PKG / "fixtures" / "negative.json"


def _load_parity():
    spec = importlib.util.spec_from_file_location("fleet_v1_runtime_parity", PARITY_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {PARITY_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


parity = _load_parity()
CONTRACT = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))

OBSERVED_PATH = PKG / "observed.py"
OBSERVATION_PATH = PKG / "observed" / "tokyo-2026-10-18.json"


def _load_observed():
    spec = importlib.util.spec_from_file_location("fleet_v1_runtime_parity_observed",
                                                  OBSERVED_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {OBSERVED_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


observed = _load_observed()
OBSERVATION = json.loads(OBSERVATION_PATH.read_text(encoding="utf-8"))


def _deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _cases(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    base = payload["base"]
    return [{**case, "observation": _deep_merge(base, case.get("patch") or {})}
            for case in payload["cases"]]


POSITIVE = _cases(POSITIVE_PATH)
NEGATIVE = _cases(NEGATIVE_PATH)


def _codes(record: dict) -> list[str]:
    return [row["code"] for row in record["refusals"]]


def _blockers(record: dict) -> list[str]:
    return [row["code"] for row in record["blockers"]]


# ---------------------------------------------------------------------------
# Positive matrix: both families, both transports where source supports them.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", POSITIVE, ids=[c["name"] for c in POSITIVE])
def test_positive_profile_projects_the_expected_matrix(case):
    out = parity.project(case["observation"])
    expected = case["expect"]

    assert out["schema"] == parity.SCHEMA
    assert out["evidence_level"] == "SOURCE_FIXTURE"
    assert out["action"] == "OBSERVE_ONLY"
    assert out["authorization"].startswith("NOT_DECIDED")
    assert out["session_owner"] == parity.SESSION_OWNER
    assert out["fallback"] is None
    assert out["refusals"] == []
    assert out["blockers"] == []

    assert out["identity"]["family"] == expected["family"]
    assert out["identity"]["agent_runtime"] == expected["agent_runtime"]
    assert out["identity"]["runtime_tag"] == expected["runtime_tag"]

    for verb, want in expected["lifecycle"].items():
        got = out["lifecycle"][verb]
        assert got["support"] == want["support"], (verb, got)
        assert got["dispatch"] == want["dispatch"], (verb, got)
        assert got["support_source"], (verb, got)

    for field in ("model", "permissionMode"):
        assert out["defaults"]["effective"][field] == expected["effective"][field]
        assert (out["defaults"]["effective"][f"{field}_provenance"]
                == expected["effective"][f"{field}_provenance"])


def test_positive_cases_cover_both_families_and_both_real_transports():
    families = {case["observation"]["provider"]["family"] for case in POSITIVE}
    transports = {case["observation"]["provider"]["transport"] for case in POSITIVE}
    assert families == {"claude", "codex"}
    assert {"cloudcli-mcp-http", "codex-app-server"} <= transports


# ---------------------------------------------------------------------------
# Negative matrix: refusal is an explicit answer, never a default.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", NEGATIVE, ids=[c["name"] for c in NEGATIVE])
def test_negative_profile_refuses_or_stays_unknown(case):
    out = parity.project(case["observation"])
    expected = case["expect"]

    for code in expected.get("refusals", []):
        assert code in _codes(out), (code, _codes(out))
    for code in expected.get("blockers", []):
        assert code in _blockers(out), (code, _blockers(out))

    dispatch = expected.get("dispatch")
    if dispatch not in (None, "MIXED"):
        assert {row["dispatch"] for row in out["lifecycle"].values()} == {dispatch}

    if dispatch == "REFUSED":
        assert out["refusals"], "a REFUSED verb must name why"
        for row in out["refusals"]:
            assert row["axis"] and row["detail"]


def test_stale_session_refuses_session_addressed_verbs_but_not_create():
    case = next(c for c in NEGATIVE if c["name"] == "stale-session")
    out = parity.project(case["observation"])
    assert "SESSION_STALE" in _codes(out)
    for verb in ("read", "send", "get_run", "interrupt"):
        assert out["lifecycle"][verb]["dispatch"] == "REFUSED"
        assert out["lifecycle"][verb]["dispatch_reason"] == "SESSION_STALE"
    # A create does not need an existing session; a stale one does not block it.
    assert out["lifecycle"]["create"]["dispatch"] == "PRECONDITIONS_MET"


def test_session_ref_runtime_axis_mismatch_is_refused():
    case = next(c for c in NEGATIVE if c["name"] == "session-ref-mismatch")
    out = parity.project(case["observation"])
    assert "SESSION_REF_MISMATCH" in _codes(out)
    for verb in ("read", "send", "get_run", "interrupt"):
        assert out["lifecycle"][verb]["dispatch"] == "REFUSED"
        assert out["lifecycle"][verb]["dispatch_reason"] == "SESSION_REF_MISMATCH"
    assert out["lifecycle"]["create"]["dispatch"] == "PRECONDITIONS_MET"


def test_absent_readiness_is_unknown_not_ready():
    case = next(c for c in NEGATIVE if c["name"] == "readiness-absent")
    out = parity.project(case["observation"])
    assert out["provider"]["readiness"] == "UNKNOWN"
    assert out["refusals"] == []
    assert out["lifecycle"]["read"]["dispatch"] == "UNKNOWN"
    assert out["lifecycle"]["read"]["dispatch_reason"] == "PROVIDER_READINESS_UNKNOWN"


def test_effective_is_never_inferred_from_requested_resolved_or_default():
    case = next(c for c in NEGATIVE if c["name"] == "effective-unproven")
    out = parity.project(case["observation"])
    effective = out["defaults"]["effective"]

    assert effective["model"] is None
    assert effective["model_provenance"] == "UNKNOWN"
    assert effective["model_source"] is None
    assert effective["permissionMode"] is None
    assert effective["permissionMode_provenance"] == "UNKNOWN"
    assert effective["permissionMode_source"] is None
    assert set(effective["rejected"]) == {"model", "permissionMode"}
    # The values ARE in requested/resolved; the point is that they did not move.
    assert out["defaults"]["requested"]["model"] is not None
    assert out["defaults"]["resolved"]["model"] is not None


def test_effective_fact_is_accepted_only_with_source_and_provenance():
    for case in POSITIVE:
        out = parity.project(case["observation"])
        effective = out["defaults"]["effective"]
        for field in ("model", "permissionMode"):
            if effective[field] is None:
                assert effective[f"{field}_provenance"] == "UNKNOWN"
                assert effective[f"{field}_source"] is None
                continue
            assert effective[f"{field}_provenance"] == "FACT"
            assert effective[f"{field}_source"]
            low = effective[f"{field}_source"].lower()
            for forbidden in ("requested", "resolved", "default", "policy"):
                assert forbidden not in low, (field, low)


# ---------------------------------------------------------------------------
# Identity, credential, and axis-specific negatives.
# ---------------------------------------------------------------------------

def test_secret_shaped_credential_ref_is_refused_and_never_echoed():
    case = next(c for c in NEGATIVE if c["name"] == "secret-shaped-credential-ref")
    out = parity.project(case["observation"])
    assert "CREDENTIAL_REF_INVALID" in _codes(out)
    blob = json.dumps(out, sort_keys=True)
    assert "sk-fixture-not-a-real-key" not in blob
    assert out["identity"]["credential_instance_ref"] is None


def test_credential_ref_rejects_raw_secret_forms():
    base = copy.deepcopy(POSITIVE[0]["observation"])
    for bad in ("sk-" + "a" * 40,
                "Bearer " + "b" * 40,
                "-----BEGIN PRIVATE KEY-----",
                "credential-instance:tokyo-shared-dev-01/vince/" + "c" * 40):
        observation = copy.deepcopy(base)
        observation["provider"]["credential_instance_ref"] = bad
        out = parity.project(observation)
        assert "CREDENTIAL_REF_INVALID" in _codes(out), bad
        assert "sk-" not in json.dumps(out)


def test_runtime_tag_is_not_the_family_or_the_agent_runtime_id():
    base = copy.deepcopy(POSITIVE[0]["observation"])

    wrong_tag = copy.deepcopy(base)
    wrong_tag["provider"]["runtime_tag"] = "agent-runtime:claude-code"
    out = parity.project(wrong_tag)
    assert "RUNTIME_TAG_MISMATCH" in _codes(out)

    wrong_family = copy.deepcopy(base)
    wrong_family["provider"]["family"] = "claude-code"
    out = parity.project(wrong_family)
    assert "FAMILY_INVALID" in _codes(out)


def test_unbound_provider_never_falls_back_to_another_family():
    case = next(c for c in NEGATIVE if c["name"] == "unbound-provider")
    out = parity.project(case["observation"])
    assert "PROVIDER_UNBOUND" in _codes(out)
    assert out["identity"]["family"] == "claude"
    assert out["fallback"] is None


def test_claude_native_adapter_is_unknown_not_invented():
    case = next(c for c in NEGATIVE if c["name"] == "claude-native-adapter-absent")
    out = parity.project(case["observation"])
    assert "PROVIDER_ADAPTER_ABSENT" in _blockers(out)
    assert {row["support"] for row in out["lifecycle"].values()} == {"UNKNOWN"}
    assert {row["dispatch"] for row in out["lifecycle"].values()} == {"UNKNOWN"}


# ---------------------------------------------------------------------------
# Purity / ownership: this file is not a Session owner and does no provider IO.
# ---------------------------------------------------------------------------

def test_projection_is_observe_only_and_has_no_provider_io():
    source = PARITY_PATH.read_text(encoding="utf-8")
    for forbidden in ("import socket", "import subprocess", "import urllib",
                      "import requests", "from mcp ", "import mcp ",
                      "from fleet_mcp", "import fleet_mcp",
                      "Popen", "os.environ", "read_text("):
        assert forbidden not in source, forbidden
    for name in ("create", "send", "interrupt", "resume", "provider_call"):
        assert not hasattr(parity, name), name
    assert parity.SESSION_OWNER.startswith("Agent Control")


def test_projection_does_not_mutate_its_input():
    observation = copy.deepcopy(POSITIVE[0]["observation"])
    before = json.dumps(observation, sort_keys=True)
    parity.project(observation)
    assert json.dumps(observation, sort_keys=True) == before


def test_projection_carries_no_clock_and_is_reproducible():
    observation = copy.deepcopy(POSITIVE[0]["observation"])
    first = parity.project(observation)
    second = parity.project(observation)
    assert first == second
    assert first["evaluated_at"] == observation["evaluated_at"]


# ---------------------------------------------------------------------------
# The contract and the module must not drift into two vocabularies.
# ---------------------------------------------------------------------------

def test_contract_and_module_vocabularies_do_not_drift():
    assert CONTRACT["schema"] == parity.SCHEMA
    assert CONTRACT["evidence_level"] == parity.EVIDENCE_LEVEL
    assert int(CONTRACT["session_ttl_seconds"]) == parity.SESSION_TTL_SECONDS
    assert list(CONTRACT["families"]) == list(parity.FAMILIES)
    assert list(CONTRACT["lifecycle_verbs"]) == list(parity.LIFECYCLE_VERBS)
    assert list(CONTRACT["support_states"]) == list(parity.SUPPORT_STATES)
    assert list(CONTRACT["dispatch_states"]) == list(parity.DISPATCH_STATES)
    assert list(CONTRACT["readiness_states"]) == list(parity.READINESS_STATES)
    assert set(CONTRACT["refusal_codes"]) == set(parity.REFUSAL_CODES)
    assert set(CONTRACT["blocker_codes"]) == set(parity.BLOCKER_CODES)

    for family, transports in CONTRACT["capability_matrix"].items():
        for transport, verbs in transports.items():
            for verb, row in verbs.items():
                state, source = parity.CAPABILITY_MATRIX[(family, transport)][verb]
                assert state == row["state"], (family, transport, verb)
                assert source == row["source"], (family, transport, verb)


def test_every_lifecycle_verb_has_a_grant_requirement():
    assert set(parity.REQUIRED_GRANT) == set(parity.LIFECYCLE_VERBS)
    assert set(CONTRACT["required_grants"]) == set(parity.LIFECYCLE_VERBS)


# ---------------------------------------------------------------------------
# Observed-vs-declared: the real read-only Tokyo snapshot of 2026-10-18.
#
# Everything below is offline. It projects a recorded snapshot; it does not
# contact a provider, read a credential, or claim a live Runtime.
# ---------------------------------------------------------------------------

def test_tokyo_observed_states_are_distinct_and_honest():
    out = observed.project_observed(OBSERVATION)
    assert out["schema"] == observed.SCHEMA
    assert out["evidence_level"] == "REAL_READ_ONLY"
    assert out["runtimes"]["claude-code"]["binding"] == "BOUND"
    assert out["runtimes"]["codex"]["binding"] == "UNBOUND"
    assert out["runtimes"]["claude-code"]["states"] == {
        "cli": "INSTALLED_ONLY",
        "direct_provider": "ABSENT",
        "agent_list": "NEEDS_LOGIN",
        "session_list": "READ_AUTHORIZED",
    }
    assert out["runtimes"]["codex"]["states"] == {
        "cli": "INSTALLED_ONLY",
        "direct_provider": "READ_AUTHORIZED",
        "agent_list": "READ_AUTHORIZED",
        "session_list": "READ_AUTHORIZED",
    }


def test_codex_effective_model_is_readback_but_mode_is_unknown():
    out = observed.project_observed(OBSERVATION)
    codex = out["runtimes"]["codex"]["effective"]
    assert codex["model"] == {
        "value": "gpt-6-astra",
        "provenance": "FACT",
        "source": "codex:thread/list",
    }
    assert codex["permissionMode"]["value"] is None
    assert codex["permissionMode"]["provenance"] == "UNKNOWN"

    claude = out["runtimes"]["claude-code"]["effective"]
    assert claude["model"]["value"] is None
    assert claude["model"]["provenance"] == "UNKNOWN"
    assert claude["permissionMode"]["value"] is None
    assert claude["permissionMode"]["provenance"] == "UNKNOWN"


def test_session_read_never_becomes_new_work_entitlement():
    out = observed.project_observed(OBSERVATION)
    assert out["entitlement"] == "NOT_INFERRED_FROM_SESSION_READ"
    for runtime in ("claude-code", "codex"):
        block = out["runtimes"][runtime]
        assert block["entitlement"] == "NOT_INFERRED_FROM_SESSION_READ"
        for verb in observed.NEW_WORK_VERBS:
            assert block["new_work"][verb]["dispatch"] != "PRECONDITIONS_MET"


def test_unbound_codex_is_refused_without_fallback():
    out = observed.project_observed(OBSERVATION)
    codex = out["runtimes"]["codex"]
    assert codex["binding"] == "UNBOUND"
    for verb in observed.NEW_WORK_VERBS:
        row = codex["new_work"][verb]
        assert row["dispatch"] == "REFUSED"
        assert "PROVIDER_UNBOUND" in row["reasons"]
    assert out["fallback"] is None


def test_needs_login_is_not_absent_or_authorized():
    out = observed.project_observed(OBSERVATION)
    claude = out["runtimes"]["claude-code"]
    assert claude["states"]["agent_list"] == "NEEDS_LOGIN"
    assert claude["states"]["session_list"] == "READ_AUTHORIZED"
    for verb in observed.NEW_WORK_VERBS:
        reasons = claude["new_work"][verb]["reasons"]
        assert "NEEDS_LOGIN" in reasons
        assert "PROVIDER_ADAPTER_ABSENT" in reasons


def test_structural_conformant_is_not_behavioral_live():
    out = observed.project_observed(OBSERVATION)
    assert out["structural_parity"]["verdict"] == "CONFORMANT"
    assert "structural" in out["structural_parity"]["scope"].lower()
    assert out["behavioral"]["state"] == "NOT_RUN"
    assert out["behavioral"]["live"] is False


def test_observed_snapshot_is_redacted():
    raw = OBSERVATION_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "sk-",
        "Bearer ",
        '"TOKEN"',
        '"title":',
        '"cwd":',
        '"argv"',
        '"environment"',
    ):
        assert forbidden not in raw, forbidden
    # No full native session id (UUID) survives into the committed snapshot.
    assert re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        raw,
    ) is None
    sessions = (OBSERVATION["runtimes"]["codex"]["session_metadata"]
                + OBSERVATION["runtimes"]["claude-code"]["session_metadata"])
    assert sessions
    for session in sessions:
        assert re.fullmatch(r"sha256:[0-9a-f]{12}", session["session_id"])


def test_observed_contract_and_module_do_not_drift():
    block = CONTRACT["observed_vs_declared"]
    assert block["observation_schema"] == observed.OBSERVATION_SCHEMA
    assert block["projection_schema"] == observed.SCHEMA
    assert block["evidence_level"] == observed.EVIDENCE_LEVEL
    assert list(block["observed_states"]) == list(observed.OBSERVED_STATES)
    assert list(block["binding_states"]) == list(observed.BINDING_STATES)
    assert list(block["new_work_verbs"]) == list(observed.NEW_WORK_VERBS)
    assert block["entitlement"] == observed.ENTITLEMENT


def test_snapshot_declared_states_match_the_projection():
    out = observed.project_observed(OBSERVATION)
    for key in ("claude-code", "codex"):
        assert OBSERVATION["runtimes"][key]["states"] == out["runtimes"][key]["states"]


def test_observed_projection_is_pure_and_deterministic():
    before = json.dumps(OBSERVATION, sort_keys=True)
    first = observed.project_observed(OBSERVATION)
    second = observed.project_observed(OBSERVATION)
    assert first == second
    assert json.dumps(OBSERVATION, sort_keys=True) == before
