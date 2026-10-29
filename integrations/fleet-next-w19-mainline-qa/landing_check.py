"""The exact-SHA landing verdict for #515.

No public landing PR exists yet, so the default answer is
``BLOCKED_OWNER / NO_LANDING_PR``. When a landing PR is published, set
``FLEET_QA_LANDING_PR`` (and optionally ``FLEET_QA_EXPECTED_SHA``) and this
script answers the same question against that exact PR:

* base must be ``main``;
* the head SHA must equal the expected SHA when one is given;
* every changed file must be under the Fleet Next monitor allowlist (a
  ``runtime/``, ``plugin/``, ``serverosd/``, ``native/`` or legacy path is a
  path-collision veto);
* hosted checks must be complete and green;
* the two #514 release blockers stay blocked — a source-only merge cannot
  close them.

This is a gate decision, not a merge. It prints JSON and always exits 0 for a
verdict; an invalid invocation exits 2.
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

RELEASE_BLOCKERS = (
    "BLOCKED_PARTIAL_UPGRADE_TRAPS_ROOT:#506",
    "BLOCKED_ARTIFACT_NOT_REPRODUCIBLE:#509",
)


def _gh_json(number: str) -> dict:
    done = subprocess.run(
        ["gh", "pr", "view", number, "--json",
         "number,title,headRefOid,baseRefName,isDraft,mergeable,"
         "mergeStateStatus,files,statusCheckRollup"],
        capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or "gh pr view failed")
    return json.loads(done.stdout)


def check(number: str | None, expected: str | None) -> dict:
    result = {
        "latest_main": os.environ.get("FLEET_QA_MAIN_SHA", compose.MAIN),
        "landing_pr": number,
        "expected_sha": expected,
        "release_gate": {
            "verdict": "BLOCKED",
            "blockers": list(RELEASE_BLOCKERS),
            "note": "source-only main landing cannot close #514's two release blockers",
        },
    }
    if not number:
        result.update({"verdict": "BLOCKED_OWNER", "code": "NO_LANDING_PR",
                       "why": "no public mainline landing PR exists for W19-B"})
        return result
    try:
        pr = _gh_json(number)
    except Exception as exc:  # noqa: BLE001 - the answer is the refusal
        result.update({"verdict": "BLOCKED_OWNER", "code": "PR_UNREADABLE",
                       "why": f"{type(exc).__name__}: {exc}"[:240]})
        return result

    files = sorted(entry["path"] for entry in pr.get("files") or [])
    forbidden = [path for path in files if not compose.allowed(path)]
    checks = pr.get("statusCheckRollup") or []
    pending = [check for check in checks
               if (check.get("status") or "").upper() not in ("COMPLETED",)
               or (check.get("conclusion") or "").upper() not in
               ("SUCCESS", "SKIPPED", "NEUTRAL")]
    result.update({
        "head": pr.get("headRefOid"),
        "base": pr.get("baseRefName"),
        "draft": pr.get("isDraft"),
        "mergeable": pr.get("mergeable"),
        "merge_state": pr.get("mergeStateStatus"),
        "files": files,
        "checks": {"total": len(checks), "not_green": len(pending)},
    })
    if pr.get("baseRefName") != "main":
        result.update({"verdict": "BLOCKED_OWNER", "code": "BASE_NOT_MAIN"})
    elif expected and pr.get("headRefOid") != expected:
        result.update({"verdict": "FAIL", "code": "HEAD_SHA_MISMATCH"})
    elif forbidden:
        result.update({"verdict": "BLOCKED_OWNER",
                       "code": "PATH_COLLISION_VETO",
                       "forbidden": forbidden})
    elif pending:
        result.update({"verdict": "BLOCKED_OWNER", "code": "CI_NOT_GREEN"})
    elif pr.get("isDraft"):
        result.update({"verdict": "BLOCKED_OWNER", "code": "DRAFT"})
    else:
        result.update({"verdict": "SIGNED_PASS", "code": "OK"})
    return result


def main() -> int:
    number = os.environ.get("FLEET_QA_LANDING_PR")
    expected = os.environ.get("FLEET_QA_EXPECTED_SHA")
    print(json.dumps(check(number, expected), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
