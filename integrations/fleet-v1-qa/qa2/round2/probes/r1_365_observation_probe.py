#!/usr/bin/env python3
"""QA2 round-2 independent probe for R1 #365 @ d043521.

Read-only against the exported exact head. The module is pure; this probe
imports it and the public synthetic fixture, signs source/observation objects
with the fixture HMAC and, for the production verifier control, with locally
generated Ed25519 keys in this process. No provider, socket, token, transcript,
environment credential or paid send.

Modes:
  controls  assert the fail-closed properties the PR claims; rc 0 iff all hold.
  findings  assert the safe contract boundary; rc 1 iff a boundary gap is
            confirmed. A confirmed gap is evidence, not a production settle.

The output's production fields must be a refusal in every case:
  production_decision == BLOCKED_PROVIDER_SOURCE
  production_eligible/send_allowed/retry_allowed/resend_allowed/settle_allowed
  == False
  synthetic_settle_allowed == False
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import hmac
import importlib.util
import json
import os
import sys
from pathlib import Path

SNAPSHOT = Path(os.environ.get(
    "R1_SNAPSHOT",
    "/data/home/vince/artifacts/skills/qa2-independent-security/"
    "2026-10-20/checkouts/r1-365-d043521")).resolve()
PKG = SNAPSHOT / "integrations" / "fleet-v1-runtime"
MODULE = PKG / "observation_contract.py"
FIXTURE = json.loads(
    (PKG / "fixtures" / "observation-contract-2026-10-20.json").read_text(
        encoding="utf-8"))
TRUST = FIXTURE["trust"]
NOW = FIXTURE["now"]
MAX_AGE = FIXTURE["max_age_seconds"]
CORE_KEY = "qa2-core-test-key"
OWNER_KEY = "qa2-owner-test-key"

spec = importlib.util.spec_from_file_location("qa2_observation_contract", MODULE)
gate = importlib.util.module_from_spec(spec)
sys.modules["qa2_observation_contract"] = gate
spec.loader.exec_module(gate)

RESULTS = []


def record(name, level, expected, actual, *, ok, note="", severity=None):
    RESULTS.append({"name": name, "level": level, "expected": expected,
                    "actual": actual, "ok": bool(ok), "note": note,
                    "severity": severity})
    print(json.dumps(RESULTS[-1], ensure_ascii=False, sort_keys=True))


def merge(base, patch):
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class HmacVerifier:
    """Test double; `production=True` is deliberately settable for the probe."""

    def __init__(self, key, *, authority, production, key_id):
        self.key = key
        self.authority = authority
        self.production = production
        self.key_id = key_id

    def verify(self, *, key_id, payload, signature):
        if key_id != self.key_id:
            return False
        return hmac.compare_digest(str(signature),
                                   gate.sign_payload(payload, self.key))


def hmac_objects(*, attempt_patch=None, source_patch=None, obs_patch=None,
                 echo_patch=None, source_key=CORE_KEY, obs_key=OWNER_KEY,
                 trust=None, sign=True):
    attempt = merge(FIXTURE["base_attempt"], attempt_patch)
    source = merge(FIXTURE["base_source_binding"], source_patch)
    observation = merge(FIXTURE["base_observation"], obs_patch)
    echo = (None if echo_patch is False
            else merge(FIXTURE["base_provider_echo"], echo_patch))
    if sign:
        source["signature"] = gate.sign_payload(source, source_key)
        observation["signature"] = gate.sign_payload(observation, obs_key)
    return attempt, source, observation, echo, (trust or TRUST)


def hmac_run(*, core_production=False, owner_production=False,
             core_key_id=None, owner_key_id=None, **kwargs):
    applied = kwargs.pop("applied", ())
    attempt, source, observation, echo, trust = hmac_objects(**kwargs)
    core = HmacVerifier(CORE_KEY, authority="core",
                        production=core_production,
                        key_id=core_key_id or trust["core_key_id"])
    owner = HmacVerifier(OWNER_KEY, authority="owner",
                         production=owner_production,
                         key_id=owner_key_id or trust["owner_key_id"])
    return gate.validate(
        attempt=attempt, observation=observation, source_binding=source,
        provider_echo=echo, core_verifier=core, owner_verifier=owner,
        trust=trust, now=NOW, max_age_seconds=MAX_AGE,
        applied_observation_ids=applied)


def production_refusal(out):
    return (out.get("production_decision") == gate.BLOCKED_PROVIDER_SOURCE
            and out.get("production_eligible") is False
            and out.get("send_allowed") is False
            and out.get("retry_allowed") is False
            and out.get("resend_allowed") is False
            and out.get("settle_allowed") is False
            and out.get("synthetic_settle_allowed") is False)


def ed25519_fixture():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat,
    )

    core_priv = Ed25519PrivateKey.generate()
    owner_priv = Ed25519PrivateKey.generate()

    def raw(priv):
        return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    return core_priv, owner_priv, raw(core_priv), raw(owner_priv)


def ed25519_objects(core_priv, owner_priv, **kwargs):
    attempt = merge(FIXTURE["base_attempt"], kwargs.get("attempt_patch"))
    source = merge(FIXTURE["base_source_binding"], kwargs.get("source_patch"))
    observation = merge(FIXTURE["base_observation"], kwargs.get("obs_patch"))
    echo = merge(FIXTURE["base_provider_echo"], kwargs.get("echo_patch"))
    source["signature"] = base64.b64encode(
        core_priv.sign(gate.canonical_bytes(source))).decode("ascii")
    observation["signature"] = base64.b64encode(
        owner_priv.sign(gate.canonical_bytes(observation))).decode("ascii")
    return attempt, source, observation, echo


# ---------------------------------------------------------------------------
# Controls: the PR's fail-closed claims must hold.
# ---------------------------------------------------------------------------

def controls():
    # 1. The synthetic contract can be satisfied, but production is refused.
    out = hmac_run()
    record("synthetic-contract-satisfied-production-refused", "SYNTHETIC_FIXTURE",
           {"synthetic_decision": "CONTRACT_SATISFIED_SYNTHETIC",
            "production": "refused",
            "blockers_include": ["BLOCKED_OWNER_SIGNED_SOURCE",
                                 "BLOCKED_PROVIDER_ECHO",
                                 "BLOCKED_S2_OBSERVATION_FIELD",
                                 "BLOCKED_COMMIT_SEAM",
                                 "BLOCKED_REPLAY_STORE"]},
           {"synthetic_decision": out.get("synthetic_decision"),
            "production_decision": out.get("production_decision"),
            "blockers": out.get("production_blockers")},
           ok=(out.get("synthetic_decision") == "CONTRACT_SATISFIED_SYNTHETIC"
               and production_refusal(out)
               and set(["BLOCKED_OWNER_SIGNED_SOURCE", "BLOCKED_PROVIDER_ECHO",
                        "BLOCKED_S2_OBSERVATION_FIELD", "BLOCKED_COMMIT_SEAM",
                        "BLOCKED_REPLAY_STORE"])
               <= set(out.get("production_blockers") or [])))

    # 2. A live echo alone does not unblock production.
    out = hmac_run(echo_patch={"live": True, "synthetic": False})
    record("live-echo-still-production-refused", "SYNTHETIC_FIXTURE",
           {"synthetic_decision": "CONTRACT_SATISFIED_SYNTHETIC",
            "owner_blocker": True,
            "echo_blocker": False},
           {"synthetic_decision": out.get("synthetic_decision"),
            "blockers": out.get("production_blockers")},
           ok=(out.get("synthetic_decision") == "CONTRACT_SATISFIED_SYNTHETIC"
               and production_refusal(out)
               and "BLOCKED_OWNER_SIGNED_SOURCE" in out["production_blockers"]
               and "BLOCKED_PROVIDER_ECHO" not in out["production_blockers"]))

    # 3. The main refusal matrix.
    cases = [
        ("self-declared-provenance",
         {"obs_patch": {"provenance": "core:agent-control"}},
         "REFUSED_SELF_DECLARED_PROVENANCE"),
        ("forged-source-signature",
         {"source_key": "wrong-key"}, "REFUSED_SOURCE_BINDING_SIGNATURE"),
        ("forged-observation-signature",
         {"obs_key": "wrong-key"}, "REFUSED_OBSERVATION_SIGNATURE"),
        ("missing-observation-id",
         {"obs_patch": {"observation_id": ""}},
         "REFUSED_MISSING_OBSERVATION_ID"),
        ("missing-client-turn-id",
         {"obs_patch": {"client_turn_id": ""}},
         "REFUSED_MISSING_CLIENT_TURN_ID"),
        ("thread-unknown",
         {"obs_patch": {"thread_known": False}}, "REFUSED_THREAD_UNKNOWN"),
        ("writer-epoch-mismatch",
         {"obs_patch": {"writer_epoch": 8}}, "REFUSED_STALE_WRITER"),
        ("observed-at-invalid",
         {"obs_patch": {"observed_at": "not-a-time"}},
         "REFUSED_OBSERVED_AT_INVALID"),
        ("observed-at-future",
         {"obs_patch": {"observed_at": "2026-10-20T05:41:00Z"}},
         "REFUSED_OBSERVED_AT_FUTURE"),
        ("observed-at-stale",
         {"obs_patch": {"observed_at": "2026-10-20T05:00:00Z"}},
         "REFUSED_OBSERVED_AT_STALE"),
        ("source-binding-expired",
         {"source_patch": {"expires_at": "2026-10-20T05:00:00Z"}},
         "REFUSED_SOURCE_BINDING_EXPIRED"),
        ("capability-inflation-cli",
         {"source_patch": {"adapter": "codex-cli"}},
         "REFUSED_CAPABILITY_MISMATCH"),
        ("capability-inflation-thread-unknown",
         {"source_patch": {"thread_known": False}},
         "REFUSED_CAPABILITY_MISMATCH"),
        ("cross-project",
         {"obs_patch": {"project_ref": "project:other"}},
         "REFUSED_CROSS_PROJECT"),
        ("changed-payload",
         {"obs_patch": {"intent_digest": "sha256:other"}},
         "REFUSED_INTENT_MISMATCH"),
        ("forged-locator",
         {"obs_patch": {"provider_locator": "turn-other"}},
         "REFUSED_LOCATOR_MISMATCH"),
        ("missing-parent", {"attempt_patch": {"parent_ref": ""}},
         "REFUSED_MISSING_PARENT"),
        ("phase-settled", {"attempt_patch": {"phase": "SETTLED"}},
         "REFUSED_NOT_UNKNOWN"),
        ("not-applied", {"obs_patch": {"state": "NOT_APPLIED"}},
         "REFUSED_NOT_APPLIED_UNAVAILABLE"),
        ("provider-echo-absent", {"echo_patch": False},
         "BLOCKED_PROVIDER_ECHO"),
        ("provider-echo-mismatch",
         {"echo_patch": {"echoed_value": "other"}},
         "REFUSED_ECHO_MISMATCH"),
    ]
    for name, kw, expected in cases:
        out = hmac_run(**kw)
        record(f"matrix-{name}", "SYNTHETIC_FIXTURE",
               {"synthetic_decision": expected, "production": "refused"},
               {"synthetic_decision": out.get("synthetic_decision"),
                "production_decision": out.get("production_decision")},
               ok=(out.get("synthetic_decision") == expected
                   and production_refusal(out)))

    # 4. Replay with a supplied applied set is refused.
    out = hmac_run(applied=("obs-contract-1",))
    record("replay-with-applied-id", "SYNTHETIC_FIXTURE",
           {"synthetic_decision": "REFUSED_REPLAY"},
           {"synthetic_decision": out.get("synthetic_decision")},
           ok=(out.get("synthetic_decision") == "REFUSED_REPLAY"
               and production_refusal(out)))

    # 5. Real Ed25519: the production verifier works and production is still
    #    blocked by the three external blockers.
    core_priv, owner_priv, core_pub, owner_pub = ed25519_fixture()
    attempt, source, observation, echo = ed25519_objects(
        core_priv, owner_priv,
        echo_patch={"live": True, "synthetic": False})
    core_verifier = gate.Ed25519Verifier(
        keys={TRUST["core_key_id"]: core_pub}, authority="core")
    owner_verifier = gate.Ed25519Verifier(
        keys={TRUST["owner_key_id"]: owner_pub}, authority="owner")
    out = gate.validate(
        attempt=attempt, observation=observation, source_binding=source,
        provider_echo=echo, core_verifier=core_verifier,
        owner_verifier=owner_verifier, trust=TRUST, now=NOW,
        max_age_seconds=MAX_AGE)
    record("real-ed25519-contract-satisfied-production-refused",
           "LOCAL_ED25519", {
               "synthetic_decision": "CONTRACT_SATISFIED_SYNTHETIC",
               "owner_blocker": False, "echo_blocker": False,
               "blockers_include": ["BLOCKED_S2_OBSERVATION_FIELD",
                                    "BLOCKED_COMMIT_SEAM",
                                    "BLOCKED_REPLAY_STORE"]},
           {"synthetic_decision": out.get("synthetic_decision"),
            "blockers": out.get("production_blockers"),
            "production_decision": out.get("production_decision")},
           ok=(out.get("synthetic_decision") == "CONTRACT_SATISFIED_SYNTHETIC"
               and production_refusal(out)
               and "BLOCKED_OWNER_SIGNED_SOURCE" not in out["production_blockers"]
               and "BLOCKED_PROVIDER_ECHO" not in out["production_blockers"]
               and {"BLOCKED_S2_OBSERVATION_FIELD", "BLOCKED_COMMIT_SEAM",
                    "BLOCKED_REPLAY_STORE"}
               <= set(out["production_blockers"])))
    # A mutated observation invalidates the Ed25519 signature.
    mutated = merge(observation, {"state": "UNKNOWN"})
    try:
        verified = owner_verifier.verify(
            key_id=mutated["key_id"], payload=mutated,
            signature=mutated["signature"])
    except gate.VerifierUnavailable:
        verified = None
    record("real-ed25519-mutated-payload-refused", "LOCAL_ED25519",
           {"signature_valid": False}, {"signature_valid": verified},
           ok=verified is False)


# ---------------------------------------------------------------------------
# Findings: contract-boundary gaps. A confirmed gap is rc 1.
# ---------------------------------------------------------------------------

def findings():
    # F1. A caller-supplied verifier that self-declares production=True and a
    #     caller-supplied trust dict can clear the owner/echo blockers. The
    #     output is still BLOCKED_PROVIDER_SOURCE, but the "synthetic cannot
    #     satisfy the production checks" claim rests on attributes the caller
    #     supplies, not on the verifier class or a Core-held trust object.
    out = hmac_run(core_production=True, owner_production=True,
                   echo_patch={"live": True, "synthetic": False})
    record("f1-synthetic-verifier-production-flag-clears-owner-blocker",
           "LOCAL_SYNTHETIC_VERIFIER",
           {"owner_blocker": True, "echo_blocker": True,
            "production": "refused"},
           {"owner_blocker": "BLOCKED_OWNER_SIGNED_SOURCE"
                              in (out.get("production_blockers") or []),
            "echo_blocker": "BLOCKED_PROVIDER_ECHO"
                            in (out.get("production_blockers") or []),
            "synthetic_decision": out.get("synthetic_decision"),
            "production_decision": out.get("production_decision"),
            "production_eligible": out.get("production_eligible")},
           ok=("BLOCKED_OWNER_SIGNED_SOURCE"
               in (out.get("production_blockers") or [])),
           note="a test HMAC verifier with production=True clears the owner "
                "blocker; production_decision remains BLOCKED_PROVIDER_SOURCE "
                "and S2/COMMIT/REPLAY blockers remain",
           severity="P2")

    # F2. The trust root is a plain caller argument; attacker key ids and
    #     verifiers are accepted as a new trust domain.
    attacker_trust = dict(TRUST, core_key_id="attacker-core",
                          owner_key_id="attacker-owner")
    out = hmac_run(core_production=True, owner_production=True,
                   core_key_id="attacker-core", owner_key_id="attacker-owner",
                   source_patch={"key_id": "attacker-core"},
                   obs_patch={"key_id": "attacker-owner"},
                   trust=attacker_trust,
                   echo_patch={"live": True, "synthetic": False})
    record("f2-caller-supplied-trust-root-substitution",
           "LOCAL_SYNTHETIC_VERIFIER",
           {"trust_root": "caller-cannot-select",
            "production": "refused"},
           {"synthetic_decision": out.get("synthetic_decision"),
            "blockers": out.get("production_blockers"),
            "production_decision": out.get("production_decision")},
           ok=("BLOCKED_OWNER_SIGNED_SOURCE"
               in (out.get("production_blockers") or [])),
           note="the validator accepts a caller-supplied trust dict and "
                "matching verifiers; production fields remain refusal",
           severity="P2")

    # F3. A revoked key id in the trust dict is ignored; there is no key
    #     revocation/rotation check in the validator.
    revoked_trust = dict(TRUST,
                         revoked_key_ids=[TRUST["owner_key_id"]])
    out = hmac_run(core_production=True, owner_production=True,
                   trust=revoked_trust,
                   echo_patch={"live": True, "synthetic": False})
    record("f3-revoked-owner-key-not-checked", "LOCAL_SYNTHETIC_VERIFIER",
           {"synthetic_decision": "REFUSED_KEY_REVOKED"},
           {"synthetic_decision": out.get("synthetic_decision"),
            "blockers": out.get("production_blockers")},
           ok=(out.get("synthetic_decision") != "CONTRACT_SATISFIED_SYNTHETIC"),
           note="`trust.revoked_key_ids` is ignored; no key revocation/epoch "
                "field exists in the validator (the doc lists rotation as an "
                "external Core dependency)",
           severity="P2")

    # F4. Missing owner/device/project is not refused: the comparisons accept
    #     None on both sides.
    empty = {"owner_ref": None, "device_ref": None, "project_ref": None}
    out = hmac_run(attempt_patch={"principal": None, "device_ref": None,
                                  "project_ref": None},
                   source_patch=empty, obs_patch=empty,
                   trust=dict(TRUST, owner_ref=None))
    record("f4-missing-owner-device-project-accepted", "LOCAL_SYNTHETIC_POLICY",
           {"synthetic_decision": "REFUSED_PRINCIPAL"},
           {"synthetic_decision": out.get("synthetic_decision"),
            "owner_ref": None, "device_ref": None, "project_ref": None},
           ok=(out.get("synthetic_decision") != "CONTRACT_SATISFIED_SYNTHETIC"),
           note="no non-empty check on principal/device/project; the contract "
                "can be satisfied with an empty owner/scope",
           severity="P2")

    # F5. The provider echo's observed_at is never parsed or freshness-checked.
    out = hmac_run(echo_patch={"live": True, "synthetic": False,
                               "observed_at": "not-a-time"})
    record("f5-provider-echo-observed-at-not-checked",
           "LOCAL_SYNTHETIC_FIXTURE",
           {"synthetic_decision": "REFUSED_ECHO_OBSERVED_AT"},
           {"synthetic_decision": out.get("synthetic_decision"),
            "echo_observed_at": "not-a-time"},
           ok=(out.get("synthetic_decision") != "CONTRACT_SATISFIED_SYNTHETIC"),
           note="echo schema includes observed_at but no parse/ordering/max-age",
           severity="P2")

    # F6. The source binding can be issued after the observation it is used
    #     for; only source-vs-now and observation-vs-now are checked.
    out = hmac_run(
        source_patch={"issued_at": "2026-10-20T05:39:32Z"},
        obs_patch={"observed_at": "2026-10-20T05:39:30Z",
                   "issued_at": "2026-10-20T05:39:35Z"},
        echo_patch={"live": True, "synthetic": False})
    record("f6-source-binding-issued-after-observation",
           "LOCAL_SYNTHETIC_FIXTURE",
           {"synthetic_decision": "REFUSED_OBSERVATION_TIME_ORDER"},
           {"synthetic_decision": out.get("synthetic_decision"),
            "source_issued_at": "2026-10-20T05:39:32Z",
            "observed_at": "2026-10-20T05:39:30Z"},
           ok=(out.get("synthetic_decision") != "CONTRACT_SATISFIED_SYNTHETIC"),
           note="no `source.issued_at <= observation.observed_at` or "
                "`observation.issued_at <= source.expires_at` check",
           severity="P2")

    # F7. The source binding nonce is not replayed; a second observation under
    #     the same signed binding is accepted.
    out1 = hmac_run(obs_patch={"observation_id": "obs-a"})
    out2 = hmac_run(obs_patch={"observation_id": "obs-b"})
    record("f7-source-binding-nonce-not-replayed", "LOCAL_SYNTHETIC_FIXTURE",
           {"second_observation": "REFUSED_REPLAY"},
           {"first": out1.get("synthetic_decision"),
            "second": out2.get("synthetic_decision"),
            "source_nonce": "nonce-source-1"},
           ok=(out2.get("synthetic_decision") != "CONTRACT_SATISFIED_SYNTHETIC"),
           note="the signed source binding has a nonce but no replay set; the "
                "observation id set is the only replay check",
           severity="P2")

    # F8. Without an applied set, the same observation id is accepted again;
    #     the output documents BLOCKED_REPLAY_STORE, so this is a residual,
    #     not a production positive.
    out = hmac_run(applied=())
    record("f8-observation-replay-without-store", "LOCAL_SYNTHETIC_FIXTURE",
           {"synthetic_decision": "REFUSED_REPLAY"},
           {"synthetic_decision": out.get("synthetic_decision"),
            "blockers": out.get("production_blockers")},
           ok=(out.get("synthetic_decision") != "CONTRACT_SATISFIED_SYNTHETIC"),
           note="the applied set is caller-supplied; the absence of a "
                "persistent replay store is documented as BLOCKED_REPLAY_STORE",
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
