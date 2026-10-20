#!/usr/bin/env python3
"""R1 #248 / S2 #245 — production observation contract tests (SYNTHETIC).

RED first: this file targets `integrations/fleet-v1-runtime/observation_contract.py`,
which does not exist at #361. The module defines the minimal production interface
for a Core-bound source declaration plus an owner-signed observation plus a
provider client-id echo.

The tests use HMAC test verifiers. A test verifier has
`authority="synthetic"`, `production=False`; it can exercise the contract but
can never become production authority. The production interface is Ed25519
(`Ed25519Verifier`), lazily imported like `context/actor.py`.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "integrations" / "fleet-v1-runtime"
MODULE_PATH = PKG / "observation_contract.py"
FIXTURE_PATH = PKG / "fixtures" / "observation-contract-2026-10-20.json"
CONTRACT_PATH = PKG / "observation-contract.yaml"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "fleet_v1_runtime_observation_contract", MODULE_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {MODULE_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_module()
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
CONTRACT = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
CORE_KEY = "synthetic-core-key-not-a-production-secret"
OWNER_KEY = "synthetic-owner-key-not-a-production-secret"
WRONG_KEY = "synthetic-wrong-key"


class SyntheticVerifier:
    """A test double. It declares itself non-production, so it cannot settle."""

    def __init__(self, key: str, *, authority="synthetic", production=False,
                 key_id="test-key"):
        self.key = key
        self.authority = authority
        self.production = production
        self.key_id = key_id

    def verify(self, *, key_id, payload, signature):
        if key_id != self.key_id:
            return False
        expected = gate.sign_payload(payload, self.key)
        return hmac.compare_digest(str(signature), expected)


def _deep_merge(base, patch):
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _run(case):
    attempt = _deep_merge(FIXTURE["base_attempt"], case.get("attempt") or {})
    observation = _deep_merge(
        FIXTURE["base_observation"], case.get("observation") or {})
    source_binding = _deep_merge(
        FIXTURE["base_source_binding"], case.get("source_binding") or {})
    echo = (None if case.get("echo", "keep") is None
            else _deep_merge(FIXTURE["base_provider_echo"], case.get("echo") or {}))

    source_key = (WRONG_KEY if case.get("source_sign_with") == "wrong-core-key"
                  else CORE_KEY)
    observation_key = (WRONG_KEY if case.get("observation_sign_with") == "wrong-owner-key"
                       else OWNER_KEY)
    source_binding["signature"] = gate.sign_payload(source_binding, source_key)
    observation["signature"] = gate.sign_payload(observation, observation_key)

    core_verifier = SyntheticVerifier(
        CORE_KEY, authority="core", production=False,
        key_id=FIXTURE["trust"]["core_key_id"])
    owner_verifier = SyntheticVerifier(
        OWNER_KEY, authority=case.get("owner_authority", "owner"),
        production=False, key_id=FIXTURE["trust"]["owner_key_id"])

    return gate.validate(
        attempt=attempt,
        observation=observation,
        source_binding=source_binding,
        provider_echo=echo,
        core_verifier=core_verifier,
        owner_verifier=owner_verifier,
        trust=FIXTURE["trust"],
        now=case.get("now", FIXTURE["now"]),
        max_age_seconds=FIXTURE["max_age_seconds"],
        applied_observation_ids=case.get("applied_observation_ids") or (),
    )


CASES = FIXTURE["cases"]
IDS = [case["name"] for case in CASES]


def _assert_production_refusal(out):
    assert out["production_decision"] == gate.BLOCKED_PROVIDER_SOURCE
    assert out["production_eligible"] is False
    assert out["send_allowed"] is False
    assert out["retry_allowed"] is False
    assert out["resend_allowed"] is False
    assert out["settle_allowed"] is False


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_case(case):
    out = _run(case)
    expected = case["expect"]

    assert out["schema"] == gate.SCHEMA
    assert out["evidence_level"] == gate.EVIDENCE_LEVEL
    _assert_production_refusal(out)
    assert out["synthetic_decision"] == expected["synthetic_decision"]
    assert out["synthetic_settle_allowed"] is False
    for blocker in expected.get("production_blockers", []):
        assert blocker in out["production_blockers"]


def test_fixture_names_are_unique():
    assert len(IDS) == len(set(IDS))


def test_no_case_resends_or_settles():
    for case in CASES:
        _assert_production_refusal(_run(case))


def test_contract_satisfied_synthetic_is_not_production():
    out = _run(next(c for c in CASES
                    if c["name"] == "contract-satisfied-synthetic"))
    assert out["synthetic_decision"] == "CONTRACT_SATISFIED_SYNTHETIC"
    _assert_production_refusal(out)
    assert out["production_blockers"] == [
        "BLOCKED_OWNER_SIGNED_SOURCE",
        "BLOCKED_PROVIDER_ECHO",
        "BLOCKED_S2_OBSERVATION_FIELD",
        "BLOCKED_COMMIT_SEAM",
        "BLOCKED_REPLAY_STORE",
    ]


def test_live_echo_alone_does_not_unblock_production():
    out = _run(next(c for c in CASES
                    if c["name"] == "provider-echo-live-still-not-production"))
    assert out["synthetic_decision"] == "CONTRACT_SATISFIED_SYNTHETIC"
    _assert_production_refusal(out)
    assert "BLOCKED_PROVIDER_ECHO" not in out["production_blockers"]
    assert "BLOCKED_OWNER_SIGNED_SOURCE" in out["production_blockers"]
    assert "BLOCKED_S2_OBSERVATION_FIELD" in out["production_blockers"]
    assert "BLOCKED_COMMIT_SEAM" in out["production_blockers"]
    assert "BLOCKED_REPLAY_STORE" in out["production_blockers"]


def test_self_declared_provenance_is_not_trusted():
    out = _run(next(c for c in CASES
                    if c["name"] == "self-declared-provenance"))
    assert out["synthetic_decision"] == "REFUSED_SELF_DECLARED_PROVENANCE"
    # The caller field is not copied into the accepted source binding.
    assert out["source_binding"]["provenance"] == "core:agent-control"


def test_source_binding_must_be_signed():
    case = copy.deepcopy(next(c for c in CASES
                              if c["name"] == "contract-satisfied-synthetic"))
    attempt = copy.deepcopy(FIXTURE["base_attempt"])
    observation = copy.deepcopy(FIXTURE["base_observation"])
    source_binding = copy.deepcopy(FIXTURE["base_source_binding"])  # no signature
    observation["signature"] = gate.sign_payload(observation, OWNER_KEY)
    out = gate.validate(
        attempt=attempt, observation=observation, source_binding=source_binding,
        provider_echo=FIXTURE["base_provider_echo"],
        core_verifier=SyntheticVerifier(CORE_KEY, authority="core",
                                   key_id=FIXTURE["trust"]["core_key_id"]),
        owner_verifier=SyntheticVerifier(OWNER_KEY, authority="owner",
                                    key_id=FIXTURE["trust"]["owner_key_id"]),
        trust=FIXTURE["trust"], now=FIXTURE["now"],
        max_age_seconds=FIXTURE["max_age_seconds"])
    assert out["synthetic_decision"] == "REFUSED_SOURCE_BINDING_SIGNATURE"
    _assert_production_refusal(out)


def test_observation_must_be_signed():
    attempt = copy.deepcopy(FIXTURE["base_attempt"])
    observation = copy.deepcopy(FIXTURE["base_observation"])  # no signature
    source_binding = copy.deepcopy(FIXTURE["base_source_binding"])
    source_binding["signature"] = gate.sign_payload(source_binding, CORE_KEY)
    out = gate.validate(
        attempt=attempt, observation=observation, source_binding=source_binding,
        provider_echo=FIXTURE["base_provider_echo"],
        core_verifier=SyntheticVerifier(CORE_KEY, authority="core",
                                   key_id=FIXTURE["trust"]["core_key_id"]),
        owner_verifier=SyntheticVerifier(OWNER_KEY, authority="owner",
                                    key_id=FIXTURE["trust"]["owner_key_id"]),
        trust=FIXTURE["trust"], now=FIXTURE["now"],
        max_age_seconds=FIXTURE["max_age_seconds"])
    assert out["synthetic_decision"] == "REFUSED_OBSERVATION_SIGNATURE"
    _assert_production_refusal(out)


def test_ed25519_verifier_declares_production_authority():
    verifier = gate.Ed25519Verifier(keys={"owner-key-fixture": b"\x00" * 32})
    assert verifier.authority == "owner"
    assert verifier.production is True


@pytest.mark.skipif(importlib.util.find_spec("cryptography") is not None,
                    reason="cryptography is available in this interpreter")
def test_ed25519_verifier_fails_closed_without_cryptography():
    verifier = gate.Ed25519Verifier(keys={"owner-key-fixture": b"\x00" * 32})
    with pytest.raises(gate.VerifierUnavailable):
        verifier.verify(key_id="owner-key-fixture", payload={},
                        signature=base64.b64encode(b"\x00" * 64).decode())


def test_module_is_pure():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for token in ("import socket", "import subprocess", "import urllib",
                  "import requests", "from mcp", "from fleet", "from runtime",
                  "os.environ", "open(", "datetime.datetime.now",
                  "datetime.datetime.utcnow", "time.time("):
        assert token not in source, f"pure module must not contain {token!r}"


def test_contract_fields_match_the_module():
    assert set(CONTRACT["objects"]["source_binding"]["fields"]) == set(gate._SOURCE_FIELDS)
    assert set(CONTRACT["objects"]["owner_observation"]["fields"]) == set(gate._OBSERVATION_FIELDS)
    assert set(CONTRACT["objects"]["provider_echo"]["fields"]) == set(gate._ECHO_FIELDS)


def test_contract_blockers_match_the_module():
    assert CONTRACT["production_blockers"] == [
        gate.BLOCKED_OWNER_SIGNED_SOURCE,
        gate.BLOCKED_PROVIDER_ECHO,
        gate.BLOCKED_S2_OBSERVATION_FIELD,
        gate.BLOCKED_COMMIT_SEAM,
        gate.BLOCKED_REPLAY_STORE,
    ]


def test_contract_no_resend_is_fail_closed():
    no_resend = CONTRACT["no_resend"]
    assert no_resend["effect_unknown"] is True
    for key in ("send_allowed", "retry_allowed", "resend_allowed",
                "settle_allowed", "not_applied_from_positive_readback",
                "exactly_once"):
        assert no_resend[key] is False, key


def test_contract_requires_live_echo_for_production():
    echo = CONTRACT["objects"]["provider_echo"]
    assert echo["production_requires"] == {"live": True, "synthetic": False}
    assert echo["echo_must_equal"] == "client_turn_id"
    assert echo["matched_on"] == "userMessage.clientId"
