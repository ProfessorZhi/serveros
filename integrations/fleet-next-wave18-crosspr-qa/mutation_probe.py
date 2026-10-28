"""Mutation control: remove one fix line and prove the QA assertion catches it.

A test that passes on the fixed tree is weak evidence unless it also fails when
the fix is absent. This program copies the composed tree to a throw-away
directory, mutates exactly one line there (never the author worktree), runs the
same real monitor case, and returns 0 only when the old defect reappears.

Modes:
  ci-device   remove #504's device-grant check in `sources.observe_ci`
  unobserved  put STATUS_UNKNOWN back into LIVE_STATUSES, so expired is "live"
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))
import compose  # noqa: E402

VICTIM = "device:victim"


def mutate(tree: Path, mode: str) -> None:
    if mode == "ci-device":
        path = tree / "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/sources.py"
        text = path.read_text(encoding="utf-8")
        needle = "why = self._grant.permits(view=VIEW_CI, device_ref=device_ref)"
        if needle not in text:
            raise SystemExit("mutation target not found: sources device grant check")
        path.write_text(text.replace(needle, "why = None  # MUTATION", 1),
                        encoding="utf-8")
        return
    if mode == "unobserved":
        path = tree / "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/contract.py"
        text = path.read_text(encoding="utf-8")
        needle = ("LIVE_STATUSES = frozenset({STATUS_ACCEPTED, STATUS_QUEUED, "
                  "STATUS_STARTED,\n                           STATUS_RUNNING})")
        if needle not in text:
            raise SystemExit("mutation target not found: LIVE_STATUSES")
        path.write_text(text.replace(
            needle,
            "LIVE_STATUSES = frozenset({STATUS_ACCEPTED, STATUS_QUEUED, "
            "STATUS_STARTED,\n                           STATUS_RUNNING, "
            "STATUS_UNKNOWN})  # MUTATION", 1), encoding="utf-8")
        return
    raise SystemExit(f"unknown mutation {mode!r}")


def run_case(tree: Path, case: str) -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("FLEET_NEXT_AGENT_MONITOR_"):
            env.pop(key)
    env["PYTHONPATH"] = os.pathsep.join([str(QA_DIR), *compose.source_paths(tree)])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QA_CASE"] = case
    env["QA_MODE"] = "deliver"
    done = subprocess.run([sys.executable, str(QA_DIR / "case_probe.py")],
                          cwd=str(QA_DIR), env=env, capture_output=True,
                          text=True, timeout=120)
    if done.returncode != 0:
        raise RuntimeError(done.stderr + done.stdout)
    return json.loads(done.stdout)


def main() -> int:
    mode = os.environ.get("QA_MUTATION", "ci-device")
    tree = Path(tempfile.mkdtemp(prefix=f"wave18-mut-{mode}-"))
    compose.archive_head(tree)
    mutate(tree, mode)
    case = "ci-forged" if mode == "ci-device" else "expired"
    result = run_case(tree, case)
    print(json.dumps({"mode": mode, "result": result}, indent=2, sort_keys=True))
    if mode == "ci-device":
        return 0 if (result["delivered"] == 1
                     and VICTIM in json.dumps(result["journal"])) else 1
    return 0 if (result["state"] == "TICKED" and result["exit"] == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
