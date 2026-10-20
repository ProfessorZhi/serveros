#!/usr/bin/env python3
"""R1 #248 / S2 #245 — client turn id readback tests (SCHEMA_ONLY).

The readback is synthetic. These tests call no provider, open no socket, read no
credential or transcript, and send nothing. They pin the one capability the
stable Codex 0.160.0 schema supports, and the four things it must never claim:

* a miss is UNKNOWN, never NOT_APPLIED;
* a client-id match with a different intent is not APPLIED;
* a duplicate client id is not an arbitrary pick;
* exactly-once and retry authorization are never produced.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "integrations" / "fleet-v1-runtime"
MODULE_PATH = PKG / "client_turn_id.py"
FIXTURE_PATH = PKG / "fixtures" / "client-turn-id-2026-10-20.json"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "fleet_v1_runtime_client_turn_id", MODULE_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {MODULE_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


client_turn_id = _load_module()
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _deep_merge(base, patch):
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _run(case):
    request = _deep_merge(FIXTURE["base_request"], case.get("request") or {})
    readback = _deep_merge(FIXTURE["base_readback"], case.get("readback") or {})
    digest = client_turn_id.intent_digest(request["intent_text"])
    return client_turn_id.observe_readback(
        client_turn_id=request["client_turn_id"],
        intent_digest=digest,
        thread_id=request["thread_id"],
        readback=readback,
        read_path=request["read_path"],
    )


CASES = FIXTURE["cases"]
IDS = [case["name"] for case in CASES]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_readback_case(case):
    out = _run(case)
    expected = case["expect"]

    assert out["schema"] == client_turn_id.SCHEMA
    assert out["evidence_level"] == client_turn_id.EVIDENCE_LEVEL
    assert out["state"] in client_turn_id.OBSERVATION_STATES
    assert out["state"] == expected["state"]
    assert out["reason"] == expected["reason"]
    assert out["provenance"] == expected["provenance"]
    assert out["lookup_miss"] is expected["lookup_miss"]
    assert out["scan_complete"] is expected["scan_complete"]
    assert out["conflict"] is expected["conflict"]
    assert out["negative_proof"] is False
    assert out["exactly_once"] is False
    assert out["retry_allowed"] is False
    assert out["source_capability"] == client_turn_id.CLIENT_TURN_ID

    if out["state"] == "APPLIED":
        assert out["source"] in client_turn_id.READ_PATHS
        assert out["matched_on"] == "userMessage.clientId"
        assert out["provider_turn_id"]
        assert out["provider_item_id"]
    else:
        assert out["state"] != "NOT_APPLIED"

    for key in ("source", "provenance", "matched_on", "provider_turn_id",
                "provider_item_id"):
        if key in expected:
            assert out[key] == expected[key], key

    if "not_contains" in expected:
        assert expected["not_contains"] not in json.dumps(out, sort_keys=True)


def test_fixture_has_unique_names():
    assert len(IDS) == len(set(IDS))


def test_every_case_is_positive_or_unknown():
    states = {case["expect"]["state"] for case in CASES}
    assert states <= set(client_turn_id.OBSERVATION_STATES)
    assert "NOT_APPLIED" not in states


def test_miss_is_unknown_and_never_frees_the_retry():
    for name in ("forged-client-id", "unknown-restart-empty-history",
                 "incomplete-pagination-miss", "thread-not-found"):
        case = next(c for c in CASES if c["name"] == name)
        out = _run(case)
        assert out["state"] == "UNKNOWN"
        assert out["negative_proof"] is False
        assert out["retry_allowed"] is False


def test_mismatch_is_a_conflict_not_an_applied_effect():
    case = next(c for c in CASES if c["name"] == "intent-mismatch")
    out = _run(case)
    assert out["state"] == "UNKNOWN"
    assert out["conflict"] is True
    assert out["reason"] == "CLIENT_ID_MATCH_INTENT_MISMATCH"


def test_duplicate_is_not_an_arbitrary_pick():
    case = next(c for c in CASES if c["name"] == "duplicate-client-id")
    out = _run(case)
    assert out["state"] == "UNKNOWN"
    assert out["conflict"] is True
    assert out["provider_turn_id"] is None
    assert out["provider_item_id"] is None


def test_incomplete_pagination_is_not_a_complete_miss():
    case = next(c for c in CASES if c["name"] == "incomplete-pagination-miss")
    out = _run(case)
    assert out["scan_complete"] is False
    assert out["lookup_miss"] is True
    assert out["state"] == "UNKNOWN"


def test_secret_shaped_client_id_is_not_echoed():
    case = next(c for c in CASES if c["name"] == "secret-shaped-client-id")
    out = _run(case)
    assert out["reason"] == "INVALID_CLIENT_TURN_ID"
    assert "sk-0123456789abcdef" not in json.dumps(out, sort_keys=True)


def test_capability_matrix():
    for case in FIXTURE["capability_cases"]:
        got = client_turn_id.capability_for(
            case["adapter"], case["verb"], thread_known=case["thread_known"])
        assert got == case["expect"], case["name"]


def test_only_codex_send_with_known_thread_is_client_turn_id():
    assert client_turn_id.capability_for(
        "codex-app-server", "send", thread_known=True) == client_turn_id.CLIENT_TURN_ID
    assert client_turn_id.capability_for(
        "codex-app-server", "send", thread_known=False) == client_turn_id.RESPONSE_ID_ONLY
    assert client_turn_id.capability_for(
        "codex-app-server", "create", thread_known=False) == client_turn_id.RESPONSE_ID_ONLY
    assert client_turn_id.capability_for(
        "botmux-agent-session", "send", thread_known=True) == client_turn_id.RESPONSE_ID_ONLY
    assert client_turn_id.capability_for(
        "cloudcli-mcp", "send", thread_known=True) == client_turn_id.NO_SOURCE


def test_synthetic_source_declares_client_turn_id_and_observes():
    digest = client_turn_id.intent_digest(FIXTURE["base_request"]["intent_text"])
    source = client_turn_id.SyntheticCodexReadbackSource(
        readback=FIXTURE["base_readback"],
        intent_digest_value=digest,
        thread_id=FIXTURE["base_request"]["thread_id"],
    )
    assert source.capability == client_turn_id.CLIENT_TURN_ID
    out = source.observe({
        "client_turn_id": FIXTURE["base_request"]["client_turn_id"],
        "provider_session_id": FIXTURE["base_request"]["thread_id"],
    })
    assert out["state"] == "APPLIED"
    assert out["negative_proof"] is False
    assert out["exactly_once"] is False


def test_capability_matrix_keeps_production_blocked():
    rows = client_turn_id.capability_matrix()
    assert len(rows) == 5
    codex_send = next(r for r in rows
                      if r["adapter"] == "codex-app-server" and r["verb"] == "send")
    assert codex_send["capability"] == client_turn_id.CLIENT_TURN_ID
    assert codex_send["production"] == client_turn_id.BLOCKED_PROVIDER_SOURCE
    assert codex_send["live_proof"] == "NOT_RUN"
    for row in rows:
        assert row["production"] == client_turn_id.BLOCKED_PROVIDER_SOURCE


def test_schema_evidence_is_pinned():
    evidence = FIXTURE["schema_evidence"]
    assert evidence["codex_version"] == "codex-cli 0.160.0"
    assert len(evidence["files"]) == 6
    for row in evidence["files"]:
        assert len(row["sha256"]) == 64
        assert row["file"].startswith("stable/v2/")


def test_intent_digest_is_stable_and_distinguishes_texts():
    a = client_turn_id.intent_digest("R1 synthetic fixed echo")
    b = client_turn_id.intent_digest("R1 synthetic fixed echo")
    c = client_turn_id.intent_digest("a different intent")
    assert a == b
    assert a != c
    assert a.startswith("sha256:")


def test_module_is_pure():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for token in ("import socket", "import subprocess", "import urllib",
                  "import requests", "from mcp", "from fleet", "from runtime",
                  "os.environ", "open(", "datetime.datetime.now", "time.time"):
        assert token not in source, f"pure module must not contain {token!r}"
