from datetime import datetime, timezone

import yaml

from intake.daily_cycle import classify
from intake.focus_source import control_join, scope_digest


def setup(tmp_path):
    card = {"id": "DEMO-1", "project": "demo", "kind": "bug", "title": "Fix the endpoint",
            "acceptance": ["the endpoint returns the requested result"]}
    path = tmp_path / "projects/demo/items/DEMO-1/card.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(card))
    row = {"id": "DEMO-1", "project": "demo", "state": "READY"}
    board = {"sections": [{"rows": [{**row, "ready_activities": [{"id": "fix", "kind": "develop"}]}]}]}
    composed = {"rows": [row], "not_produced": {}}
    grant = {"ref": "g1", "projects": ["demo"], "items": ["DEMO-1"], "actions": ["develop"],
             "scope_digests": {"DEMO-1": scope_digest(card)}, "revoked": False,
             "valid_from": "2026-10-06T00:00:00Z", "expires_at": "2026-10-07T00:00:00Z",
             "budget": {"wall_seconds": 60, "max_attempts": 1, "max_output_bytes": 1024, "exhausted": False}}
    return path, card, composed, board, grant


def test_current_scope_and_planned_activity_reach_existing_policy(tmp_path):
    _, _, composed, board, grant = setup(tmp_path)
    grants = {"g1": grant}
    result = control_join(composed, board, records_root=tmp_path, grants=grants)
    row = result["rows"][0]
    assert result["control_join"][0]["gaps"] == []
    assert row["next_action"] == "develop" and row["grant_ref"] == "g1"
    assert classify(row, grants, now=datetime(2026, 10, 6, 12, tzinfo=timezone.utc))["disposition"] == "ELIGIBLE"


def test_changed_acceptance_cannot_copy_the_grant_digest_to_pass(tmp_path):
    path, card, composed, board, grant = setup(tmp_path)
    card["acceptance"].append("change production routing")
    path.write_text(yaml.safe_dump(card))
    result = control_join(composed, board, records_root=tmp_path, grants={"g1": grant})
    assert classify(result["rows"][0], {"g1": grant}, now=datetime(2026, 10, 6, 12, tzinfo=timezone.utc))["reason"] == "SCOPE_CHANGED"


def test_ambiguous_work_or_grants_do_not_choose_for_the_person(tmp_path):
    _, _, composed, board, grant = setup(tmp_path)
    board["sections"][0]["rows"][0]["ready_activities"].append({"id": "ship", "kind": "publish"})
    result = control_join(composed, board, records_root=tmp_path, grants={"g1": grant, "g2": grant})
    assert "next_action" not in result["rows"][0]
    assert "grant_ref" not in result["rows"][0]
    assert len(result["control_join"][0]["gaps"]) == 2
