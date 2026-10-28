#!/usr/bin/env python3
"""Contract tests for the Tokyo-local Fleet Next Goal Supervisor.

These tests exist because the supervisor's whole value is that it keeps
telling the truth when nobody is watching:

  * a second process must not become a second supervisor;
  * a reboot must resume, not restart, and must never re-send a Run whose
    outcome was never observed;
  * a session that is already RUNNING_AGENT must never be messaged;
  * a source that did not answer must be UNKNOWN, never green;
  * the in-flight cap counts work that is already running, not only work
    this supervisor started.

Every test below is written against the module's public surface only.  The
module itself lives in ``integrations/fleet-next-local-goal-supervisor/`` and
is loaded by path so the directory keeps the owner-assigned name (hyphens are
not importable).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "integrations" / "fleet-next-local-goal-supervisor"
MODULE_PATH = PLUGIN_DIR / "supervisor.py"


def _load():
    assert MODULE_PATH.is_file(), f"missing supervisor module at {MODULE_PATH}"
    spec = importlib.util.spec_from_file_location(
        "fleet_next_local_goal_supervisor", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def sv():
    return _load()


# ---------------------------------------------------------------------------
# Single instance
# ---------------------------------------------------------------------------


def test_second_process_cannot_acquire_the_same_lock(sv, tmp_path):
    lock_path = tmp_path / "goal.lock"

    first = sv.SingleInstanceLock(lock_path)
    second = sv.SingleInstanceLock(lock_path)

    assert first.acquire() is True
    try:
        assert second.acquire() is False, "two supervisors must not hold one goal"
    finally:
        first.release()

    # The lock is a real OS lock: once released, the next process may hold it.
    third = sv.SingleInstanceLock(lock_path)
    assert third.acquire() is True
    third.release()


def test_lock_is_released_by_the_os_when_the_process_dies(sv, tmp_path):
    """A stale PID file must not block recovery — flock dies with the holder."""
    lock_path = tmp_path / "goal.lock"
    lock_path.write_text(json.dumps({"pid": 999999, "boot_epoch": "old"}))

    lock = sv.SingleInstanceLock(lock_path)
    assert lock.acquire() is True
    lock.release()


# ---------------------------------------------------------------------------
# Persistent state and recovery
# ---------------------------------------------------------------------------


def test_state_roundtrip_preserves_identity_and_cursor(sv, tmp_path):
    store = sv.StateStore(tmp_path)
    state = sv.new_state(goal_id="fleet-next-v1-long-horizon-closure", boot_epoch="boot-a")
    state["cursor"]["github_head"] = "e69fc0337ae5d33000478e3d5162077f525bc8cc"
    state["cursor"]["last_tick_at"] = "2026-10-29T02:00:00+08:00"

    store.save(state)
    loaded = store.load()

    assert loaded["schema"] == sv.SCHEMA
    assert loaded["goal_id"] == "fleet-next-v1-long-horizon-closure"
    assert loaded["cursor"]["github_head"] == "e69fc0337ae5d33000478e3d5162077f525bc8cc"


def test_state_save_is_atomic_no_partial_file_left(sv, tmp_path):
    store = sv.StateStore(tmp_path)
    store.save(sv.new_state(goal_id="g", boot_epoch="b"))

    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"atomic write left temp files: {leftovers}"


def test_reboot_resumes_and_preserves_cursor_and_ledger(sv, tmp_path):
    store = sv.StateStore(tmp_path)
    state = sv.new_state(goal_id="g", boot_epoch="boot-a")
    state["cursor"]["github_head"] = "abc123"
    state["dispatch_ledger"]["task-1"] = {
        "status": "DISPATCHED",
        "run_id": "run-1",
        "session_ref": "device:d|claude|s1",
    }
    store.save(state)

    resumed = sv.resume_state(store.load(), boot_epoch="boot-b")

    assert resumed["boot_epoch"] == "boot-b"
    assert resumed["resume_count"] == 1
    assert resumed["cursor"]["github_head"] == "abc123"
    assert resumed["dispatch_ledger"]["task-1"]["run_id"] == "run-1"


def test_unobserved_dispatch_is_not_resent_after_reboot(sv):
    """A Run whose outcome was never read stays UNKNOWN and is not re-sent."""
    state = sv.new_state(goal_id="g", boot_epoch="boot-a")
    state["dispatch_ledger"]["task-1"] = {
        "status": "DISPATCH_ACKED_UNOBSERVED",
        "session_ref": "device:d|claude|s1",
        "run_id": None,
    }

    decision = sv.plan_tick(
        observation=sv.empty_observation(),
        state=state,
        config=sv.default_config(),
        now="2026-10-29T03:00:00+08:00",
    )

    assert decision["decision"] == "NO_DISPATCH"
    assert "task-1" in decision["held_unknown"]
    assert decision["would_dispatch"] == []


# ---------------------------------------------------------------------------
# In-flight cap and dispatch safety
# ---------------------------------------------------------------------------


def _obs(**kw):
    base = {
        "github": {"state": "OK", "head_sha": "abc", "open_prs": [], "issues": {}},
        "local_agents": {"state": "OK", "agents": []},
        "fleet": {"state": "UNKNOWN", "sessions": [], "reason": "no snapshot"},
    }
    base.update(kw)
    return base


def test_in_flight_cap_counts_sessions_already_running(sv):
    """Three RUNNING_AGENT sessions already saturate the cap."""
    state = sv.new_state(goal_id="g", boot_epoch="b")
    config = sv.default_config()
    config["dispatch"]["enabled"] = True
    config["dispatch"]["provider"] = "verified-test-provider"

    obs = _obs(
        fleet={
            "state": "FRESH",
            "sessions": [
                {"session_ref": "device:d|claude|a", "classification": "RUNNING_AGENT"},
                {"session_ref": "device:d|claude|b", "classification": "RUNNING_AGENT"},
                {"session_ref": "device:d|claude|c", "classification": "RUNNING_AGENT"},
            ],
        }
    )

    decision = sv.plan_tick(observation=obs, state=state, config=config, now="t")

    assert decision["effective_inflight"] == 3
    assert decision["decision"] == "NO_DISPATCH_CAP"
    assert decision["would_dispatch"] == []


def test_running_agent_is_refused_as_a_target(sv):
    """An idle-eligible owner is fine; a RUNNING_AGENT is never a target."""
    state = sv.new_state(goal_id="g", boot_epoch="b")
    config = sv.default_config()
    config["dispatch"]["enabled"] = True
    config["dispatch"]["provider"] = "verified-test-provider"

    obs = _obs(
        fleet={
            "state": "FRESH",
            "sessions": [
                {"session_ref": "device:d|claude|running", "classification": "RUNNING_AGENT"},
                {"session_ref": "device:d|claude|idle", "classification": "MANAGED_IDLE"},
            ],
        }
    )

    decision = sv.plan_tick(observation=obs, state=state, config=config, now="t")
    targets = [a["session_ref"] for a in decision["would_dispatch"]]

    assert "device:d|claude|idle" in targets
    assert "device:d|claude|running" not in targets
    assert decision["refused"]["device:d|claude|running"] == "RUNNING_AGENT"


def test_dispatch_is_idempotent_by_task_key(sv):
    state = sv.new_state(goal_id="g", boot_epoch="b")
    config = sv.default_config()
    config["dispatch"]["enabled"] = True
    config["dispatch"]["provider"] = "verified-test-provider"

    obs = _obs(
        fleet={
            "state": "FRESH",
            "sessions": [
                {"session_ref": "device:d|claude|idle", "classification": "MANAGED_IDLE"}
            ],
        }
    )

    first = sv.plan_tick(observation=obs, state=state, config=config, now="t1")
    assert len(first["would_dispatch"]) == 1
    task_key = first["would_dispatch"][0]["task_key"]

    # Apply the decision the way the runner would, then tick again unchanged.
    sv.record_dispatch(state, first["would_dispatch"][0], now="t1", outcome="DISPATCHED")
    second = sv.plan_tick(observation=obs, state=state, config=config, now="t2")

    assert second["would_dispatch"] == []
    assert second["decision"] in {"NO_DISPATCH", "NO_DISPATCH_CAP"}
    assert task_key in second["already_known"]


def test_dispatch_is_disabled_by_default(sv):
    state = sv.new_state(goal_id="g", boot_epoch="b")
    config = sv.default_config()
    obs = _obs(
        fleet={
            "state": "FRESH",
            "sessions": [
                {"session_ref": "device:d|claude|idle", "classification": "MANAGED_IDLE"}
            ],
        }
    )

    decision = sv.plan_tick(observation=obs, state=state, config=config, now="t")

    assert config["dispatch"]["enabled"] is False
    assert decision["decision"] == "DRY_RUN_DISPATCH"
    assert decision["would_dispatch"], "dry run still shows the plan it would execute"


def test_enabled_dispatch_without_a_verified_provider_is_blocked(sv):
    state = sv.new_state(goal_id="g", boot_epoch="b")
    config = sv.default_config()
    config["dispatch"]["enabled"] = True
    config["dispatch"]["provider"] = None

    decision = sv.plan_tick(observation=_obs(), state=state, config=config, now="t")

    assert decision["decision"] == "BLOCKED_ACTIVATION"
    assert decision["gap"] == "NO_VERIFIED_DISPATCH_PROVIDER"


# ---------------------------------------------------------------------------
# Sources: UNKNOWN is not green
# ---------------------------------------------------------------------------


def test_missing_fleet_snapshot_is_unknown_not_ok(sv, tmp_path):
    fleet = sv.observe_fleet_snapshot(tmp_path / "absent.json", max_age_seconds=120, now_epoch=1000)

    assert fleet["state"] == "UNKNOWN"
    assert fleet["sessions"] == []
    assert fleet["reason"]


def test_stale_fleet_snapshot_is_stale_not_ok(sv, tmp_path):
    snapshot = tmp_path / "fleet.json"
    snapshot.write_text(json.dumps({"written_at_epoch": 10, "sessions": []}))

    fleet = sv.observe_fleet_snapshot(snapshot, max_age_seconds=120, now_epoch=1000)

    assert fleet["state"] == "STALE"


def test_github_observation_parses_real_shape_and_never_invents(sv):
    calls = []

    def runner(argv):
        calls.append(argv)
        if argv[:3] == ["gh", "pr", "list"]:
            return json.dumps(
                [
                    {
                        "number": 507,
                        "isDraft": True,
                        "headRefName": "feat/fleet-next-rc-wave18-20261029",
                        "headRefOid": "deadbeef",
                    }
                ]
            )
        if argv[:2] == ["git", "rev-parse"]:
            return "e69fc0337ae5d33000478e3d5162077f525bc8cc"
        raise AssertionError(f"unexpected command: {argv}")

    obs = sv.observe_github(
        repo="ProfessorZhi/serveros",
        cwd="/nonexistent",
        runner=runner,
        prs=[507],
    )

    assert obs["state"] == "OK"
    assert obs["head_sha"] == "e69fc0337ae5d33000478e3d5162077f525bc8cc"
    assert obs["open_prs"][0]["number"] == 507
    assert obs["open_prs"][0]["head_sha"] == "deadbeef"


def test_source_failure_degrades_that_source_only(sv):
    def runner(argv):
        raise OSError("gh unavailable")

    obs = sv.observe_github(repo="ProfessorZhi/serveros", cwd="/x", runner=runner, prs=[507])
    assert obs["state"] == "UNKNOWN"
    assert obs["error"]


# ---------------------------------------------------------------------------
# Backoff, kill switch, receipts
# ---------------------------------------------------------------------------


def test_backoff_is_exponential_and_capped(sv):
    assert sv.compute_backoff(0, base=60, cap=1800) == 60
    assert sv.compute_backoff(1, base=60, cap=1800) == 120
    assert sv.compute_backoff(2, base=60, cap=1800) == 240
    assert sv.compute_backoff(99, base=60, cap=1800) == 1800


def test_stop_file_is_a_kill_switch(sv, tmp_path):
    stop = tmp_path / "STOP"
    stop.write_text("operator stop\n")

    decision = sv.plan_tick(
        observation=_obs(),
        state=sv.new_state(goal_id="g", boot_epoch="b"),
        config=sv.default_config(),
        now="t",
        stop_file=stop,
    )

    assert decision["decision"] == "STOPPED"


def test_tick_writes_heartbeat_and_receipt_with_observed_ids_only(sv, tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    config = sv.default_config()
    config["sources"]["github_repo"] = "ProfessorZhi/serveros"
    config["sources"]["fleet_snapshot"] = str(tmp_path / "absent.json")

    def runner(argv):
        if argv[:2] == ["git", "rev-parse"]:
            return "abc123"
        if argv[:2] == ["gh", "pr"]:
            return json.dumps([])
        if argv[:2] == ["gh", "issue"]:
            return json.dumps({"number": 478, "state": "OPEN"})
        raise AssertionError(argv)

    receipt = sv.tick(
        config=config,
        state_dir=state_dir,
        runner=runner,
        now_epoch=1000,
        now_iso="2026-10-29T03:00:00+08:00",
        boot_epoch="boot-a",
    )

    heartbeat = json.loads((state_dir / "heartbeat.json").read_text())
    assert heartbeat["last_tick_iso"] == "2026-10-29T03:00:00+08:00"
    assert heartbeat["boot_epoch"] == "boot-a"

    assert receipt["schema"] == sv.RECEIPT_SCHEMA
    assert receipt["goal_id"] == "fleet-next-v1-long-horizon-closure"
    assert receipt["observation"]["github"]["head_sha"] == "abc123"
    # Nothing is invented: the fleet source did not answer, so it is UNKNOWN.
    assert receipt["observation"]["fleet"]["state"] == "UNKNOWN"


def test_receipt_path_is_confined_to_the_state_dir(sv, tmp_path):
    with pytest.raises(sv.PathEscape):
        sv.confine(tmp_path, tmp_path.parent / "outside.json")


def test_fleet_snapshot_is_normalized_and_stamped(sv, tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text(
        json.dumps(
            {
                "sessions": [
                    {
                        "session_ref": "device:d|claude|s1",
                        "device_ref": "device:d",
                        "runtime": "claude",
                        "classification": "RUNNING_AGENT",
                        "title": "wave18 rc",
                    }
                ]
            }
        )
    )
    out = tmp_path / "snap.json"

    payload = sv.write_fleet_snapshot(
        raw, out, now_epoch=1000, producer="test-agent-turn"
    )

    assert payload["written_at_epoch"] == 1000
    assert payload["producer"] == "test-agent-turn"
    assert payload["sessions"][0]["classification"] == "RUNNING_AGENT"
    assert json.loads(out.read_text())["sessions"][0]["session_ref"] == "device:d|claude|s1"


def test_tick_resolves_boot_epoch_when_omitted(sv, tmp_path):
    """A live tick supplies no boot id; the module must resolve the kernel's."""
    config = sv.default_config()
    config["sources"]["fleet_snapshot"] = ""

    def runner(argv):
        if argv[:2] == ["git", "rev-parse"]:
            return "abc123"
        if argv[:3] == ["gh", "pr", "list"]:
            return json.dumps([])
        if argv[:2] == ["gh", "issue"]:
            return json.dumps({"number": int(argv[3]), "state": "OPEN"})
        raise AssertionError(argv)

    receipt = sv.tick(
        config=config,
        state_dir=tmp_path / "s",
        runner=runner,
        now_epoch=1000,
        now_iso="2026-10-29T03:00:00+08:00",
    )

    assert receipt["boot_epoch"] not in (None, "", "None")


def test_ci_observation_classifies_pass_fail_and_pending(sv):
    rows = {
        507: [{"name": "a", "state": "SUCCESS", "bucket": "pass", "link": "l1"}],
        509: [
            {"name": "Suite (shard 1/4)", "state": "SUCCESS", "bucket": "pass", "link": "l2"},
            {"name": "Suite (shard 4/4)", "state": "FAILURE", "bucket": "fail", "link": "l3"},
        ],
        508: [{"name": "Suite (shard 4/4)", "state": "PENDING", "bucket": "pending", "link": "l4"}],
    }

    def runner(argv):
        assert argv[:3] == ["gh", "pr", "checks"]
        return json.dumps(rows[int(argv[3])])

    ci = sv.observe_ci(repo="ProfessorZhi/serveros", prs=[507, 509, 508], runner=runner)

    assert ci["state"] == "OK"
    assert ci["prs"]["507"]["verdict"] == "PASS"
    assert ci["prs"]["509"]["verdict"] == "FAIL"
    assert ci["prs"]["509"]["failing"][0]["name"] == "Suite (shard 4/4)"
    assert ci["prs"]["508"]["verdict"] == "PENDING"


def test_ci_with_no_checks_is_unknown_not_pass(sv):
    ci = sv.observe_ci(repo="r", prs=[510], runner=lambda argv: "[]")
    assert ci["prs"]["510"]["verdict"] == "UNKNOWN"


def test_ci_command_failure_is_unknown_not_pass(sv):
    def runner(argv):
        raise RuntimeError("gh unavailable")

    ci = sv.observe_ci(repo="r", prs=[509], runner=runner)
    assert ci["state"] == "UNKNOWN"
    assert ci["prs"]["509"]["verdict"] == "UNKNOWN"
