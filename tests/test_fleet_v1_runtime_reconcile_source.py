#!/usr/bin/env python3
"""R1 #248 / S2 #245 — fail-closed reconcile-source gate tests.

The fixture is synthetic. These tests call no provider, open no socket, read no
credential or transcript, and send nothing. They pin the production default:

    uncertainty → no send, no retry, no automatic settle, BLOCKED_PROVIDER_SOURCE

and the negative checks the task asks for: forged locator, cross-owner,
cross-project, changed payload and missing parent, plus device crossing,
unsigned/bad signature, replay, stale writer and a provider `NOT_APPLIED` that
this readback cannot prove.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "integrations" / "fleet-v1-runtime"
MODULE_PATH = PKG / "reconcile_source_gate.py"
FIXTURE_PATH = PKG / "fixtures" / "reconcile-source-2026-10-20.json"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "fleet_v1_runtime_reconcile_source_gate", MODULE_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {MODULE_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_module()
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
TEST_KEY = FIXTURE["test_key"]


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
    source = _deep_merge(FIXTURE["base_source"], case.get("source") or {})
    if case.get("sign", True):
        observation["signature"] = gate.sign(observation, TEST_KEY)
    if "signature_override" in case:
        observation["signature"] = case["signature_override"]
    return gate.evaluate(
        attempt, observation, source,
        mode=case.get("mode", "synthetic"),
        test_key=TEST_KEY,
        applied_observation_ids=case.get("applied_observation_ids") or (),
    )


CASES = FIXTURE["cases"]
IDS = [case["name"] for case in CASES]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_case(case):
    out = _run(case)
    expected = case["expect"]

    assert out["schema"] == gate.SCHEMA
    assert out["evidence_level"] == gate.EVIDENCE_LEVEL
    assert out["decision"] == expected["decision"]
    assert out["s2_verdict"] == expected["s2_verdict"]
    assert out["settle_allowed"] is expected["settle_allowed"]
    assert out["owner_signed"] is expected["owner_signed"]
    if "signature_authority" in expected:
        assert out["signature_authority"] == expected["signature_authority"]

    # The production default is invariant across every case.
    assert out["send_allowed"] is False
    assert out["retry_allowed"] is False
    assert out["production_eligible"] is False
    assert out["production_decision"] == gate.BLOCKED_PROVIDER_SOURCE


def test_fixture_names_are_unique():
    assert len(IDS) == len(set(IDS))


def test_production_is_blocked_even_with_a_valid_synthetic_signature():
    case = next(c for c in CASES if c["name"] == "production-mode-always-blocked")
    out = _run(case)
    assert out["decision"] == gate.BLOCKED_OWNER_SIGNATURE
    assert out["s2_verdict"] is None
    assert out["settle_allowed"] is False
    assert out["signature_authority"] is None


def test_uncertainty_never_sends_or_retries_or_settles():
    for case in CASES:
        out = _run(case)
        if out["decision"] != gate.SYNTHETIC_ACCEPTED_APPLIED:
            assert out["settle_allowed"] is False
        assert out["send_allowed"] is False
        assert out["retry_allowed"] is False


def test_no_case_settles_not_applied():
    for case in CASES:
        out = _run(case)
        assert out["s2_verdict"] != "SETTLE_NOT_APPLIED"
        assert out["decision"] != "REFUSED_NOT_APPLIED"  # not a settle word


def test_forged_locator_is_refused():
    case = next(c for c in CASES if c["name"] == "forged-locator")
    out = _run(case)
    assert out["decision"] == gate.REFUSED_LOCATOR_MISMATCH
    assert out["settle_allowed"] is False


def test_cross_owner_is_refused():
    case = next(c for c in CASES if c["name"] == "cross-owner")
    out = _run(case)
    assert out["decision"] == gate.REFUSED_PRINCIPAL
    assert out["s2_verdict"] == gate.S2_REFUSE_PRINCIPAL


def test_cross_project_is_refused():
    case = next(c for c in CASES if c["name"] == "cross-project")
    out = _run(case)
    assert out["decision"] == gate.REFUSED_CROSS_PROJECT


def test_changed_payload_is_refused():
    case = next(c for c in CASES if c["name"] == "changed-payload")
    out = _run(case)
    assert out["decision"] == gate.REFUSED_INTENT_MISMATCH
    assert out["settle_allowed"] is False


def test_missing_parent_is_refused():
    case = next(c for c in CASES if c["name"] == "missing-parent")
    out = _run(case)
    assert out["decision"] == gate.REFUSED_MISSING_PARENT


def test_replayed_observation_is_refused():
    case = next(c for c in CASES if c["name"] == "replayed-observation")
    out = _run(case)
    assert out["decision"] == gate.REFUSED_REPLAY
    assert out["settle_allowed"] is False


def test_not_applied_is_unavailable_from_this_readback():
    case = next(c for c in CASES
                if c["name"] == "not-applied-is-not-available-from-this-readback")
    out = _run(case)
    assert out["decision"] == gate.REFUSED_NOT_APPLIED_UNAVAILABLE
    assert out["s2_verdict"] == gate.S2_REFUSE_UNKNOWN
    assert out["retry_allowed"] is False


def test_unsigned_and_bad_signature_are_owner_signature_blocks():
    for name in ("unsigned-observation", "bad-signature"):
        out = _run(next(c for c in CASES if c["name"] == name))
        assert out["decision"] == gate.BLOCKED_OWNER_SIGNATURE
        assert out["owner_signed"] is False


def test_signature_payload_ignores_unsigned_fields_and_covers_signed_ones():
    observation = copy.deepcopy(FIXTURE["base_observation"])
    observation["signature"] = gate.sign(observation, TEST_KEY)
    assert gate.signature_valid(observation, TEST_KEY)

    unrelated = dict(observation, note="an unsigned field changed")
    assert gate.signature_payload(unrelated) == gate.signature_payload(observation)
    assert gate.signature_valid(unrelated, TEST_KEY)

    signed = dict(observation, state="UNKNOWN")
    assert gate.signature_payload(signed) != gate.signature_payload(observation)
    assert not gate.signature_valid(signed, TEST_KEY)


def test_capability_matrix_records_the_blocked_default():
    rows = gate.capability_matrix()
    assert len(rows) == len(FIXTURE["matrix_expect"])
    by_key = {(r["adapter"], r["verb"]): r for r in rows}
    for expected in FIXTURE["matrix_expect"]:
        row = by_key[(expected["adapter"], expected["verb"])]
        assert row["capability"] == expected["capability"]
        assert row["owner_signed_observation"] == expected["owner_signed_observation"]
        assert row["production_decision"] == expected["production_decision"]
        assert row["production_decision"] == gate.BLOCKED_PROVIDER_SOURCE


def test_claude_code_cli_is_not_a_provider_readback():
    row = next(r for r in gate.capability_matrix()
               if r["adapter"] == "claude-code-cli")
    assert row["pre_send_id"] == "--session-id <uuid>"
    assert row["capability"] == "CLI_ONLY_NOT_WIRED"
    assert row["production_decision"] == gate.BLOCKED_PROVIDER_SOURCE


def test_evaluate_rejects_unknown_mode_and_non_mapping_attempt():
    with pytest.raises(ValueError):
        gate.evaluate({}, None, None, mode="pretend")
    with pytest.raises(ValueError):
        gate.evaluate("not-a-mapping", None, None, mode="synthetic")


def test_module_is_pure():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for token in ("import socket", "import subprocess", "import urllib",
                  "import requests", "from mcp", "from fleet", "from runtime",
                  "os.environ", "open(", "datetime.datetime.now", "time.time"):
        assert token not in source, f"pure module must not contain {token!r}"
