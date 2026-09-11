#!/usr/bin/env python3
"""Validate the executable ownership/path contract for the ServerOS plugin."""

from __future__ import annotations

import json
import sys
from pathlib import Path


EXPECTED_ROUTES = {
    "information.capture": "information-management",
    "work.item": "work-management",
    "test.environment": "test-management",
    "workspace.reconcile": "workspace-management",
    "ability.distribute": "ability-management",
    "federation.feishu": "feishu-space-management",
    "report.publish": "feishu-report",
}

# The minimum the contract must forbid. This was a set-EQUALITY test against the
# same three literals, which failed in both directions:
#
#   * it could not notice the contract was INCOMPLETE, because the literal was
#     incomplete in exactly the same way -- authority.md forbids four fallbacks
#     (the fourth being the cwd fallback) and neither side said so;
#   * it rejected a contract that was MORE correct. Adding the missing fourth
#     entry turned this validator red.
#
# Both are the same defect: the validator's own snapshot was behaving as the
# authority. A subset test keeps the useful half (a required entry removed ->
# FAIL) and drops the half that made correctness look like breakage.
#
# This validator still cannot decide completeness -- it has no access to
# authority.md. That comparison lives in architecture_check.py A3
# (FORBIDDEN_ROOTS_INCOMPLETE). Stated here so a green run is not over-read.
REQUIRED_FORBIDDEN_ROOTS = {
    "workspace/artifacts",
    "workspace/toolchains",
    "~/files",
    "cwd-fallback",
}


def validate_contract(contract: dict) -> list[str]:
    errors: list[str] = []
    if contract.get("schema") != "serveros/ability-contract.v1":
        errors.append("unsupported contract schema")
    if contract.get("product") != "Agent Engineering Workbench":
        errors.append("product must be the Agent Engineering Workbench")
    if contract.get("dispatcher") != "serveros":
        errors.append("serveros must be the single dispatcher")
    if contract.get("common_path_is_mandatory") is not False:
        errors.append("common path must not be a mandatory pipeline")
    if contract.get("normal_task_full_scan") is not False:
        errors.append("normal tasks must not trigger full-domain scans")
    if contract.get("serverosd_status") != "future-interface":
        errors.append("serverosd must remain a future interface boundary")
    if contract.get("milestone_history") != "immutable" or contract.get("current_view") != "mutable-projection":
        errors.append("milestones must be immutable and current view mutable")
    if contract.get("item_path") != "records/projects/<project>/items/<ITEM>":
        errors.append("Item path must use project/items/ITEM")
    if contract.get("artifact_path") != "artifacts/work-items/<project>/<ITEM>/<RUN>":
        errors.append("artifact path must be project/Item/Run scoped")
    if contract.get("worktrees_per_item") != "0..N":
        errors.append("an Item must allow zero to many worktrees")
    routes = contract.get("routes")
    if routes != EXPECTED_ROUTES:
        errors.append("routed domains must match the seven ServerOS specialist owners")
    if isinstance(routes, dict) and len(set(routes.values())) != len(routes):
        errors.append("each routed domain must have one distinct owner")
    forbidden = set(contract.get("forbidden_roots") or ())
    missing = sorted(REQUIRED_FORBIDDEN_ROOTS - forbidden)
    if missing:
        errors.append(f"forbidden fallback roots missing: {missing}")
    return errors


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    default = Path(__file__).resolve().parent.parent / "references" / "serveros-contract.json"
    path = Path(argv[0]).resolve() if argv else default
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL", "errors": [str(exc)]}, ensure_ascii=False))
        return 2
    errors = validate_contract(contract)
    print(json.dumps({"status": "PASS" if not errors else "FAIL", "contract": str(path), "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
