#!/usr/bin/env python3
"""QA2 independent probe for R1 #356 @ b4020b7.

Read-only against the exact PR snapshot. The gate is pure; this probe imports it
and the synthetic fixture, signs observations with the fixture's test key, and
prints both the control set and the adversarial witnesses.

No provider, network, token, transcript, socket, subprocess or paid send.

Usage:
  python3 r1_probe.py --mode controls
  python3 r1_probe.py --mode findings

Exit code:
  controls: 0 iff every safe-behavior control holds.
  findings: 1 iff at least one adversarial witness confirms a gap.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import sys
from pathlib import Path

SNAPSHOT = Path(os.environ.get(
    "R1_SNAPSHOT",
    "/data/home/vince/artifacts/skills/qa2-independent-security/"
    "2026-10-20/checkouts/r1-356-b4020b7")).resolve()
PKG = SNAPSHOT / "integrations" / "fleet-v1-runtime"
MODULE_PATH = PKG / "reconcile_source_gate.py"
FIXTURE_PATH = PKG / "fixtures" / "reconcile-source-2026-10-20.json"


def _load():
    spec = importlib.util.spec_from_file_location(
        "qa2_fleet_v1_runtime_reconcile_source_gate", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load()
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
TEST_KEY = FIXTURE["test_key"]
RESULTS = []


def _deep_merge(base, patch):
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def run(*, attempt_patch=None, observation_patch=None, source_patch=None,
        sign=True, signature_override=None, mode="synthetic", applied=()):
    attempt = _deep_merge(FIXTURE["base_attempt"], attempt_patch)
    observation = _deep_merge(FIXTURE["base_observation"], observation_patch)
    source = _deep_merge(FIXTURE["base_source"], source_patch)
    if sign:
        observation["signature"] = gate.sign(observation, TEST_KEY)
    if signature_override is not None:
        observation["signature"] = signature_override
    return gate.evaluate(
        attempt, observation, source, mode=mode, test_key=TEST_KEY,
        applied_observation_ids=applied)


def record(name, level, expected, actual, *, ok, note="", severity=None):
    RESULTS.append({
        "name": name, "level": level, "expected": expected, "actual": actual,
        "ok": bool(ok), "note": note, "severity": severity,
    })
    print(json.dumps(RESULTS[-1], ensure_ascii=False, sort_keys=True))


def controls():
    # 1. Every fixture case: the author's expectation, plus the production
    #    invariants that must hold in every case.
    for case in FIXTURE["cases"]:
        out = run(attempt_patch=case.get("attempt"),
                  observation_patch=case.get("observation"),
                  source_patch=case.get("source"),
                  sign=case.get("sign", True),
                  signature_override=case.get("signature_override"),
                  mode=case.get("mode", "synthetic"),
                  applied=case.get("applied_observation_ids") or ())
        expected = case["expect"]
        ok = (out.get("decision") == expected["decision"]
              and out.get("s2_verdict") == expected["s2_verdict"]
              and out.get("settle_allowed") is expected["settle_allowed"]
              and out.get("owner_signed") is expected["owner_signed"]
              and out.get("send_allowed") is False
              and out.get("retry_allowed") is False
              and out.get("production_eligible") is False
              and out.get("production_decision")
              == gate.BLOCKED_PROVIDER_SOURCE)
        record(f"fixture-{case['name']}", "SYNTHETIC_FIXTURE",
               {"decision": expected["decision"],
                "settle_allowed": expected["settle_allowed"],
                "owner_signed": expected["owner_signed"]},
               {"decision": out.get("decision"),
                "settle_allowed": out.get("settle_allowed"),
                "owner_signed": out.get("owner_signed")},
               ok=ok)

    # 2. Production mode is blocked even with a valid synthetic signature.
    out = run(mode="production")
    record("production-mode-blocks-valid-signature", "SYNTHETIC_FIXTURE",
           {"decision": "BLOCKED_OWNER_SIGNATURE", "settle_allowed": False,
            "owner_signed": False},
           {"decision": out.get("decision"),
            "settle_allowed": out.get("settle_allowed"),
            "owner_signed": out.get("owner_signed")},
           ok=(out.get("decision") == "BLOCKED_OWNER_SIGNATURE"
               and out.get("settle_allowed") is False
               and out.get("owner_signed") is False))

    # 3. No source / no observation in synthetic mode.
    out = gate.evaluate(FIXTURE["base_attempt"], None, None,
                        mode="synthetic", test_key=TEST_KEY)
    record("no-source-synthetic", "SYNTHETIC_FIXTURE",
           {"decision": "BLOCKED_PROVIDER_SOURCE", "settle_allowed": False},
           {"decision": out.get("decision"),
            "settle_allowed": out.get("settle_allowed")},
           ok=(out.get("decision") == "BLOCKED_PROVIDER_SOURCE"
               and out.get("settle_allowed") is False))
    # Replace the whole observation with None is not expressible via the merge;
    # call the gate directly.
    out = gate.evaluate(FIXTURE["base_attempt"], None, FIXTURE["base_source"],
                        mode="synthetic", test_key=TEST_KEY)
    record("no-observation-synthetic", "SYNTHETIC_FIXTURE",
           {"decision": "BLOCKED_OWNER_SIGNATURE", "settle_allowed": False},
           {"decision": out.get("decision"),
            "settle_allowed": out.get("settle_allowed")},
           ok=(out.get("decision") == "BLOCKED_OWNER_SIGNATURE"
               and out.get("settle_allowed") is False))

    # 4. Purity: the module has no IO/credential/process import.
    source = MODULE_PATH.read_text(encoding="utf-8")
    forbidden = ("import socket", "import subprocess", "import urllib",
                 "import requests", "from mcp", "from fleet", "from runtime",
                 "os.environ", "open(", "datetime.datetime.now", "time.time")
    hits = [token for token in forbidden if token in source]
    record("module-is-pure", "CODE", {"forbidden_hits": []},
           {"forbidden_hits": hits}, ok=not hits)


def findings():
    # F1. An APPLIED observation with an empty (or absent) observation_id
    #     bypasses the replay check (`if observation_id and ...`).
    for label, patch in (("empty", {"observation_id": ""}),
                         ("absent", {"observation_id": None})):
        out = run(observation_patch=patch, applied=("",))
        record(f"f1-replay-with-{label}-observation-id", "SYNTHETIC_FIXTURE",
               {"decision": "REFUSED_REPLAY", "settle_allowed": False},
               {"decision": out.get("decision"),
                "settle_allowed": out.get("settle_allowed"),
                "observation_id": out.get("observation_id")},
               ok=(out.get("decision") == "REFUSED_REPLAY"),
               note="the replay check is skipped when the id is empty; the "
                    "signed payload still validates",
               severity="P1")
    # The same observation id is refused when it is non-empty.
    out = run(applied=("obs-fixture-1",))
    record("f1-control-nonempty-id-refused", "SYNTHETIC_FIXTURE",
           {"decision": "REFUSED_REPLAY"},
           {"decision": out.get("decision")},
           ok=out.get("decision") == "REFUSED_REPLAY")

    # F2. The signed `source` field is never compared with the source
    #     capability or the source object. A forged non-empty source passes
    #     the gate and the S2 kernel's only source check (non-empty).
    out = run(observation_patch={"source": "forged:source"})
    record("f2-signed-source-not-bound", "SYNTHETIC_FIXTURE",
           {"decision": "REFUSED_SOURCE_MISMATCH", "settle_allowed": False},
           {"decision": out.get("decision"),
            "settle_allowed": out.get("settle_allowed"),
            "signed_source": "forged:source",
            "source_capability": out.get("source_capability")},
           ok=out.get("settle_allowed") is False,
           note="source_capability=CLIENT_TURN_ID and observation.source="
                "forged:source; the gate never reads the signed source",
           severity="P2")

    # F3. observed_at is signed but never parsed, ordered or freshness-checked.
    out = run(observation_patch={"observed_at": "not-a-time"})
    record("f3-observed-at-not-validated", "SYNTHETIC_FIXTURE",
           {"decision": "REFUSED_OBSERVATION_TIME", "settle_allowed": False},
           {"decision": out.get("decision"),
            "settle_allowed": out.get("settle_allowed"),
            "observed_at": "not-a-time"},
           ok=out.get("settle_allowed") is False,
           note="no parse, no clock and no max-age; the kernel does not check "
                "observed_at either",
           severity="P2")

    # F4. The synthetic result carries the production field names:
    #     owner_signed=True, s2_verdict=SETTLE_APPLIED, settle_allowed=True.
    out = run()
    record("f4-synthetic-result-production-field-names", "SYNTHETIC_FIXTURE",
           {"owner_signed": False,
            "s2_verdict": "not SETTLE_APPLIED",
            "settle_allowed": False},
           {"owner_signed": out.get("owner_signed"),
            "s2_verdict": out.get("s2_verdict"),
            "s2_status": out.get("s2_status"),
            "settle_allowed": out.get("settle_allowed"),
            "production_eligible": out.get("production_eligible"),
            "production_decision": out.get("production_decision"),
            "mode": out.get("mode"),
            "signature_authority": out.get("signature_authority")},
           ok=(out.get("owner_signed") is False
               and out.get("s2_verdict") != "SETTLE_APPLIED"),
           note="a test HMAC is labelled owner_signed=True and carries the "
                "kernel's real SETTLE_APPLIED token; only the qualifier fields "
                "(mode, signature_authority, production_eligible) prevent a "
                "consumer from treating it as a production settle",
           severity="P1")

    # F5. The signed payload has no writer fence (owner/epoch/token). A signed
    #     observation replayed against a new writer epoch still settles when
    #     the applied set is not supplied.
    out = run(attempt_patch={"writer": {"epoch": 8},
                             "current_writer": {"epoch": 8}})
    record("f5-writer-epoch-not-signed", "SYNTHETIC_FIXTURE",
           {"decision": "REFUSED_STALE_WRITER", "settle_allowed": False},
           {"decision": out.get("decision"),
            "settle_allowed": out.get("settle_allowed"),
            "signed_fields_include_epoch": "epoch" in gate._SIGNED_FIELDS},
           ok=out.get("settle_allowed") is False,
           note="the observation is unchanged and still validates across an "
                "epoch 7 -> 8 writer move; the writer fence is not in the "
                "signed bytes. Non-empty observation ids are the only replay "
                "defence, and F1 shows that id can be emptied.",
           severity="P2")
    # Control: the same cross-epoch replay is caught when the non-empty id is
    # present in the applied set.
    out = run(attempt_patch={"writer": {"epoch": 8},
                             "current_writer": {"epoch": 8}},
              applied=("obs-fixture-1",))
    record("f5-control-replay-id-caught", "SYNTHETIC_FIXTURE",
           {"decision": "REFUSED_REPLAY"},
           {"decision": out.get("decision")},
           ok=out.get("decision") == "REFUSED_REPLAY")

    # F6. The source gate trusts the source object's declared `capability` and
    #     never cross-checks `thread_known`, adapter or verb. A `create` shaped
    #     source (unknown thread) or a CLI adapter can declare CLIENT_TURN_ID
    #     and pass.
    for label, patch in (
            ("unknown-thread", {"thread_known": False}),
            ("cli-adapter-declares-turn-id",
             {"adapter": "codex-cli", "verb": "any"})):
        out = run(source_patch=patch)
        record(f"f6-source-declaration-not-cross-checked-{label}",
               "SYNTHETIC_FIXTURE",
               {"decision": "BLOCKED_PROVIDER_SOURCE",
                "settle_allowed": False},
               {"decision": out.get("decision"),
                "settle_allowed": out.get("settle_allowed"),
                "adapter": "python-dict",
                "thread_known": patch.get("thread_known", True),
                "source_capability": out.get("source_capability")},
               ok=out.get("decision") == "BLOCKED_PROVIDER_SOURCE",
               note="only `present` and `capability` are read; the capability "
                    "matrix's own adapter/verb/thread_known distinctions are "
                    "not enforced by the gate",
               severity="P2")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("controls", "findings"), required=True)
    args = parser.parse_args()
    if args.mode == "controls":
        controls()
    else:
        findings()
    failed = [r for r in RESULTS if not r["ok"]]
    print(json.dumps({"mode": args.mode, "total": len(RESULTS),
                      "failed": len(failed),
                      "failed_names": [r["name"] for r in failed]},
                     ensure_ascii=False, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
