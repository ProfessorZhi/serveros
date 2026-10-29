"""RED baseline: reproduce the four Wave16 defects on RC #500.

The three #501 xfails were:

* a CI watch can deliver under an ungranted typed device;
* an expired/unknown watch reaches the supervisor as ``TICKED`` exit 0;
* a delivery-id-only watch cannot deliver (and has no named refusal).

The fourth is the un-named refusal path: #500 lets a delivery-id-only fact reach
#423 and fail as ``run.runId is required`` instead of refusing it by name.

Exit 0 means all four old behaviours were reproduced. It is evidence of the
before picture, never a product pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))
import compose  # noqa: E402

DEV = "device:tokyo-qa-1"
VICTIM = "device:victim"


def run_case(tree: Path, case: str, *, mode: str = "deliver") -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("FLEET_NEXT_AGENT_MONITOR_"):
            env.pop(key)
    env["PYTHONPATH"] = os.pathsep.join([str(QA_DIR), *compose.source_paths(tree)])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QA_CASE"] = case
    env["QA_MODE"] = mode
    done = subprocess.run([sys.executable, str(QA_DIR / "case_probe.py")],
                          cwd=str(QA_DIR), env=env, capture_output=True,
                          text=True, timeout=120)
    if done.returncode != 0:
        raise RuntimeError(done.stderr + done.stdout)
    return json.loads(done.stdout)


def main() -> int:
    tree = compose.baseline_500()
    result = {
        "ci_forged": run_case(tree, "ci-forged"),
        "expired": run_case(tree, "expired"),
        "delivery_only_native": run_case(tree, "delivery-only-native"),
        "delivery_only_no_run": run_case(tree, "delivery-only-no-run"),
    }
    print(json.dumps(result, indent=2, sort_keys=True))

    forged = result["ci_forged"]
    forged_ok = (forged["delivered"] == 1
                 and VICTIM in json.dumps(forged["journal"]))
    expired_ok = (result["expired"]["state"] == "TICKED"
                  and result["expired"]["exit"] == 0)
    native_ok = result["delivery_only_native"]["delivered"] == 0
    no_run = result["delivery_only_no_run"]
    no_run_ok = (no_run["state"] == "UNCONFIRMED"
                 and no_run["watch"].get("receipt") == "DELIVERY_UNKNOWN")
    return 0 if all((forged_ok, expired_ok, native_ok, no_run_ok)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
