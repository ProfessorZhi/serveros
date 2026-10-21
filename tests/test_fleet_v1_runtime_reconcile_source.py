#!/usr/bin/env python3
"""R1 #248 / S2 #245 — hardened fail-closed reconcile-source gate tests.

v2 after QA2 #360. The fixture is synthetic. These tests call no provider,
open no socket, read no credential or transcript, and send nothing.

The production contract is deliberately boring:

    decision = production_decision = BLOCKED_PROVIDER_SOURCE
    production_eligible = send_allowed = retry_allowed = settle_allowed = false
    owner_signed = false; s2_verdict = null; s2_status = BLOCKED_PROVIDER_SOURCE

Synthetic evidence lives under `synthetic_*` and can never be read as a
production settle. The QA2 adversarial witnesses are kept, not deleted:
empty/absent observation id, forged signed source, unparseable/future/stale
observed_at, contradictory source declarations, and a writer epoch move.
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
    now = case["now"] if "now" in case else FIXTURE["now"]
    return gate.evaluate(
        attempt, observation, source,
        mode=case.get("mode", "synthetic"),
        test_key=TEST_KEY,
        applied_observation_ids=case.get("applied_observation_ids") or (),
        now=now,
        max_age_seconds=FIXTURE["max_age_seconds"],
    )


CASES = FIXTURE["cases"]
IDS = [case["name"] for case in CASES]


def _assert_production_refusal(out):
    assert out["decision"] == gate.BLOCKED_PROVIDER_SOURCE
    assert out["production_decision"] == gate.BLOCKED_PROVIDER_SOURCE
    assert out["production_eligible"] is False
    assert out["send_allowed"] is False
    assert out["retry_allowed"] is False
    assert out["settle_allowed"] is False
    assert out["owner_signed"] is False
    assert out["s2_verdict"] is None
    assert out["s2_status"] == gate.BLOCKED_PROVIDER_SOURCE


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_case(case):
    out = _run(case)
    expected = case["expect"]

    assert out["schema"] == gate.SCHEMA
    assert out["evidence_level"] == gate.EVIDENCE_LEVEL
    _assert_production_refusal(out)

    assert out["synthetic_decision"] == expected["synthetic_decision"]
    assert out["synthetic_settle_allowed"] is expected["synthetic_settle_allowed"]
    assert out["synthetic_signature_valid"] is expected["synthetic_signature_valid"]
    assert out["production_reason"] == expected["production_reason"]


def test_fixture_names_are_unique():
    assert len(IDS) == len(set(IDS))


def test_every_case_is_a_production_refusal():
    for case in CASES:
        out = _run(case)
        _assert_production_refusal(out)


def test_no_case_settles_production_or_emits_a_kernel_settle_verdict():
    for case in CASES:
        out = _run(case)
        assert out["settle_allowed"] is False
        assert out["s2_verdict"] is None
        assert out["s2_status"] == gate.BLOCKED_PROVIDER_SOURCE
        assert out["synthetic_decision"] != "SETTLE_APPLIED"


def test_production_mode_is_blocked_even_with_a_valid_synthetic_signature():
    case = next(c for c in CASES if c["name"] == "production-mode-always-blocked")
    out = _run(case)
    assert out["production_reason"] == gate.BLOCKED_OWNER_SIGNATURE
    assert out["synthetic_decision"] is None
    assert out["synthetic_settle_allowed"] is False
    assert out["synthetic_signature_valid"] is False


# ---------------------------------------------------------------------------
# QA2 #360 adversarial witnesses, migrated to the v2 contract.
# ---------------------------------------------------------------------------

def test_qa2_p1_synthetic_result_never_carries_a_production_settle():
    case = next(c for c in CASES
                if c["name"] == "qa2-p1-synthetic-production-field-names")
    out = _run(case)
    _assert_production_refusal(out)
    assert out["synthetic_decision"] == "SYNTHETIC_ACCEPTED_APPLIED"
    assert out["synthetic_settle_allowed"] is True
    assert out["synthetic_signature_valid"] is True
    assert out["signature_authority"] == gate.SIGNATURE_AUTHORITY
    # The production reader has nothing to settle on.
    assert out["owner_signed"] is False
    assert out["settle_allowed"] is False
    assert out["s2_verdict"] is None


@pytest.mark.parametrize("name", ["qa2-p1-empty-observation-id",
                                  "qa2-p1-absent-observation-id"])
def test_qa2_p1_empty_or_absent_observation_id_is_refused(name):
    out = _run(next(c for c in CASES if c["name"] == name))
    assert out["synthetic_decision"] == gate.REFUSED_MISSING_OBSERVATION_ID
    assert out["synthetic_settle_allowed"] is False
    _assert_production_refusal(out)


def test_qa2_p2_signed_source_must_match_the_capability():
    out = _run(next(c for c in CASES
                    if c["name"] == "qa2-p2-forged-signed-source"))
    assert out["synthetic_decision"] == gate.REFUSED_SOURCE_MISMATCH
    assert out["synthetic_settle_allowed"] is False
    _assert_production_refusal(out)


@pytest.mark.parametrize("name,decision", [
    ("qa2-p2-observed-at-invalid", "REFUSED_OBSERVED_AT_INVALID"),
    ("qa2-p2-observed-at-future", "REFUSED_OBSERVED_AT_FUTURE"),
    ("qa2-p2-observed-at-stale", "REFUSED_OBSERVED_AT_STALE"),
])
def test_qa2_p2_observed_at_is_parsed_and_bounded(name, decision):
    out = _run(next(c for c in CASES if c["name"] == name))
    assert out["synthetic_decision"] == decision
    assert out["synthetic_settle_allowed"] is False
    _assert_production_refusal(out)


@pytest.mark.parametrize("name", ["qa2-p2-source-thread-known-false",
                                  "qa2-p2-source-cli-adapter"])
def test_qa2_p2_source_declaration_is_cross_checked(name):
    out = _run(next(c for c in CASES if c["name"] == name))
    assert out["synthetic_decision"] == gate.BLOCKED_PROVIDER_SOURCE
    assert out["production_reason"] == gate.BLOCKED_PROVIDER_SOURCE
    _assert_production_refusal(out)


def test_qa2_p2_writer_epoch_is_in_the_signed_payload():
    assert "writer_owner" in gate._SIGNED_FIELDS
    assert "writer_epoch" in gate._SIGNED_FIELDS
    assert "writer_token" in gate._SIGNED_FIELDS
    out = _run(next(c for c in CASES
                    if c["name"] == "qa2-p2-writer-epoch-7-to-8"))
    assert out["synthetic_decision"] == gate.REFUSED_STALE_WRITER
    assert out["synthetic_settle_allowed"] is False
    _assert_production_refusal(out)


def test_qa2_missing_now_is_blocked():
    out = _run(next(c for c in CASES if c["name"] == "qa2-missing-now"))
    assert out["synthetic_decision"] == gate.BLOCKED_TIME_UNTRUSTED
    assert out["production_reason"] == gate.BLOCKED_TIME_UNTRUSTED
    assert out["synthetic_signature_valid"] is False
    _assert_production_refusal(out)


# ---------------------------------------------------------------------------
# Semantic controls retained from v1.
# ---------------------------------------------------------------------------

def test_forged_locator_is_refused():
    out = _run(next(c for c in CASES if c["name"] == "forged-locator"))
    assert out["synthetic_decision"] == gate.REFUSED_LOCATOR_MISMATCH


def test_cross_owner_is_refused():
    out = _run(next(c for c in CASES if c["name"] == "cross-owner"))
    assert out["synthetic_decision"] == gate.REFUSED_PRINCIPAL


def test_cross_project_is_refused():
    out = _run(next(c for c in CASES if c["name"] == "cross-project"))
    assert out["synthetic_decision"] == gate.REFUSED_CROSS_PROJECT


def test_changed_payload_is_refused():
    out = _run(next(c for c in CASES if c["name"] == "changed-payload"))
    assert out["synthetic_decision"] == gate.REFUSED_INTENT_MISMATCH


def test_missing_parent_is_refused():
    out = _run(next(c for c in CASES if c["name"] == "missing-parent"))
    assert out["synthetic_decision"] == gate.REFUSED_MISSING_PARENT


def test_replayed_observation_is_refused():
    out = _run(next(c for c in CASES if c["name"] == "replayed-observation"))
    assert out["synthetic_decision"] == gate.REFUSED_REPLAY


def test_not_applied_is_unavailable_from_this_readback():
    out = _run(next(c for c in CASES
                    if c["name"] == "not-applied-is-not-available-from-this-readback"))
    assert out["synthetic_decision"] == gate.REFUSED_NOT_APPLIED_UNAVAILABLE
    assert out["retry_allowed"] is False


def test_unsigned_and_bad_signature_are_blocked():
    for name in ("unsigned-observation", "bad-signature"):
        out = _run(next(c for c in CASES if c["name"] == name))
        assert out["synthetic_decision"] == gate.BLOCKED_OWNER_SIGNATURE
        assert out["synthetic_signature_valid"] is False


def test_signature_payload_ignores_unsigned_fields_and_covers_signed_ones():
    observation = copy.deepcopy(FIXTURE["base_observation"])
    observation["signature"] = gate.sign(observation, TEST_KEY)
    assert gate.signature_valid(observation, TEST_KEY)

    unrelated = dict(observation, note="an unsigned field changed")
    assert gate.signature_payload(unrelated) == gate.signature_payload(observation)
    assert gate.signature_valid(unrelated, TEST_KEY)

    for field, value in (("state", "UNKNOWN"), ("writer_epoch", 8),
                         ("observed_at", "2026-10-20T05:00:00Z"),
                         ("source", "forged:source")):
        changed = dict(observation, **{field: value})
        assert gate.signature_payload(changed) != gate.signature_payload(observation)
        assert not gate.signature_valid(changed, TEST_KEY)


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
    assert row["capability"] == gate.CLI_ONLY_NOT_WIRED
    assert row["production_decision"] == gate.BLOCKED_PROVIDER_SOURCE


def test_evaluate_rejects_unknown_mode_and_non_mapping_attempt():
    with pytest.raises(ValueError):
        gate.evaluate({}, None, None, mode="pretend")
    with pytest.raises(ValueError):
        gate.evaluate("not-a-mapping", None, None, mode="synthetic",
                      now=FIXTURE["now"])


@pytest.mark.parametrize("bad", [0, -1, 3601, "300"])
def test_max_age_is_bounded(bad):
    with pytest.raises(ValueError):
        gate.evaluate(FIXTURE["base_attempt"], None, None, mode="synthetic",
                      now=FIXTURE["now"], max_age_seconds=bad)


def test_module_is_pure():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for token in ("import socket", "import subprocess", "import urllib",
                  "import requests", "from mcp", "from fleet", "from runtime",
                  "os.environ", "open(", "datetime.datetime.now",
                  "datetime.datetime.utcnow", "time.time("):
        assert token not in source, f"pure module must not contain {token!r}"
