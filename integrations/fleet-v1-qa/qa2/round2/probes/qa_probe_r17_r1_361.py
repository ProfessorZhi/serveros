#!/usr/bin/env python3
"""Independent re-test of R1 #356 QA2 witnesses against the #361 fix.

Same attack cases as QA2 R1, evaluated under the v2 contract:
  * production fields are ALWAYS a refusal (no synthetic owner_signed /
    SETTLE_APPLIED / settle_allowed);
  * empty/absent observation id, forged source, bad/future/stale time,
    capability/thread mismatch, old writer epoch, replay.
The fixture is the public test fixture in the PR; no QA2 probe source is used.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

TREE = Path(os.environ["QA_TREE"]).resolve()
sys.path.insert(0, str(TREE / "integrations" / "fleet-v1-runtime"))

spec = importlib.util.spec_from_file_location(
    "qa_r1_gate_v2", TREE / "integrations" / "fleet-v1-runtime" / "reconcile_source_gate.py")
gate = importlib.util.module_from_spec(spec)
sys.modules["qa_r1_gate_v2"] = gate
spec.loader.exec_module(gate)

FIXTURE = json.loads((TREE / "integrations" / "fleet-v1-runtime" / "fixtures"
                      / "reconcile-source-2026-10-20.json").read_text(encoding="utf-8"))
KEY = FIXTURE["test_key"]
NOW = FIXTURE["now"]
MAX_AGE = FIXTURE["max_age_seconds"]

FAIL = 0
TOTAL = 0


def check(name, cond, detail=""):
    global FAIL, TOTAL
    TOTAL += 1
    if cond:
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name, detail)


def clone(value):
    return json.loads(json.dumps(value))


def run(*, observation_changes=None, source_changes=None, attempt_changes=None,
        sign=True, mode="synthetic", applied=(), now=NOW):
    attempt = clone(FIXTURE["base_attempt"])
    attempt.update(attempt_changes or {})
    observation = clone(FIXTURE["base_observation"])
    observation.update(observation_changes or {})
    source = clone(FIXTURE["base_source"])
    source.update(source_changes or {})
    if sign:
        observation["signature"] = gate.sign(observation, KEY)
    return gate.evaluate(attempt, observation, source, mode=mode,
                         test_key=KEY, applied_observation_ids=applied,
                         now=now, max_age_seconds=MAX_AGE)


def production_refusal(out):
    return (out.get("decision") == gate.BLOCKED_PROVIDER_SOURCE
            and out.get("production_decision") == gate.BLOCKED_PROVIDER_SOURCE
            and out.get("production_eligible") is False
            and out.get("send_allowed") is False
            and out.get("retry_allowed") is False
            and out.get("settle_allowed") is False
            and out.get("owner_signed") is False
            and out.get("s2_verdict") is None
            and out.get("s2_status") == gate.BLOCKED_PROVIDER_SOURCE)


# 1. A valid synthetic HMAC may prove APPLIED only in synthetic_* fields.
out = run()
check("synth/valid-decision",
      out.get("synthetic_decision") == gate.SYNTHETIC_ACCEPTED_APPLIED)
check("synth/valid-settle-flag", out.get("synthetic_settle_allowed") is True)
check("synth/production-fields-refusal", production_refusal(out), repr(out))
check("synth/never-owner-signed", out.get("owner_signed") is False)
check("synth/never-settle-allowed", out.get("settle_allowed") is False)
check("synth/no-production-verdict", out.get("s2_verdict") is None)

# 2. Production mode never settles even with a valid synthetic signature.
prod = run(mode="production")
check("prod/blocked", production_refusal(prod), repr(prod))
check("prod/reason", prod.get("production_reason") == gate.BLOCKED_OWNER_SIGNATURE)
check("prod/no-synthetic-decision", prod.get("synthetic_decision") is None)

# 3. Empty/absent observation id -> missing id, not replay-skip.
for label, value in (("empty", ""), ("absent", None)):
    out = run(observation_changes={"observation_id": value})
    check(f"id/{label}", out.get("synthetic_decision") == gate.REFUSED_MISSING_OBSERVATION_ID,
          repr(out.get("synthetic_decision")))
    check(f"id/{label}/no-settle", out.get("settle_allowed") is False)

# 4. Forged signed source vs capability.
out = run(observation_changes={"source": "forged:source"})
check("source/forged", out.get("synthetic_decision") == gate.REFUSED_SOURCE_MISMATCH,
      repr(out.get("synthetic_decision")))

# 5. Time: unparseable, future, stale.
out = run(observation_changes={"observed_at": "not-a-time"})
check("time/invalid", out.get("synthetic_decision") == gate.REFUSED_OBSERVED_AT_INVALID)
out = run(observation_changes={"observed_at": "2026-10-20T05:41:00Z"})
check("time/future", out.get("synthetic_decision") == gate.REFUSED_OBSERVED_AT_FUTURE)
out = run(observation_changes={"observed_at": "2026-10-20T05:00:00Z"})
check("time/stale", out.get("synthetic_decision") == gate.REFUSED_OBSERVED_AT_STALE)

# 6. Capability / thread cross-check.
out = run(source_changes={"thread_known": False})
check("source/thread-unknown", out.get("synthetic_decision") == gate.BLOCKED_PROVIDER_SOURCE)
out = run(source_changes={"adapter": "codex-cli"})
check("source/cli-adapter", out.get("synthetic_decision") == gate.BLOCKED_PROVIDER_SOURCE)
out = run(source_changes={"verb": "create"})
check("source/create-verb", out.get("synthetic_decision") == gate.BLOCKED_PROVIDER_SOURCE)

# 7. Writer epoch/token is in the signed payload and the current writer wins.
out = run(attempt_changes={
    "writer": {"owner": "actor:owner:fixture", "epoch": 8, "token": "tok-8"},
    "current_writer": {"owner": "actor:owner:fixture", "epoch": 8, "token": "tok-8"}})
check("writer/old-epoch", out.get("synthetic_decision") == gate.REFUSED_STALE_WRITER,
      repr(out.get("synthetic_decision")))
# Changing a signed writer field after signing must invalidate the signature.
obs = clone(FIXTURE["base_observation"])
obs["signature"] = gate.sign(obs, KEY)
obs["writer_epoch"] = 8
out = gate.evaluate(clone(FIXTURE["base_attempt"]), obs,
                    clone(FIXTURE["base_source"]), mode="synthetic",
                    test_key=KEY, now=NOW, max_age_seconds=MAX_AGE)
check("writer/signed-field-mutation",
      out.get("synthetic_decision") == gate.BLOCKED_OWNER_SIGNATURE,
      repr(out.get("synthetic_decision")))

# 8. Replay with a non-empty id; and no source.
out = run(applied=("obs-fixture-1",))
check("replay/nonempty", out.get("synthetic_decision") == gate.REFUSED_REPLAY)
out = run(source_changes={"present": False})
check("source/none", out.get("synthetic_decision") == gate.BLOCKED_PROVIDER_SOURCE)
check("source/none-production-refusal", production_refusal(out))

print(f"TOTAL {TOTAL} FAIL {FAIL}")
raise SystemExit(1 if FAIL else 0)
