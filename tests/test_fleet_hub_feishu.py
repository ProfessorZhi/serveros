"""The hub's deeper Feishu binding: destinations, self-check, owner-only buttons."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "integrations" / "fleet-next-agent-monitor",
              ROOT / "integrations" / "fleet-hub-watch"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fleet_hub_watch import cards, feishu  # noqa: E402
from fleet_hub_watch.__main__ import act_on_button  # noqa: E402
from fleet_mcp.inbox import EventInbox  # noqa: E402
from fleet_mcp.monitor_control import MonitorControl  # noqa: E402

OWNER = "ou_" + "a" * 32
NOW = datetime(2026, 11, 7, 2, 0, tzinfo=timezone.utc)


def runner(table):
    def run(argv):
        for needle, out in table.items():
            if needle in argv:
                return SimpleNamespace(returncode=0, stdout=out)
        return SimpleNamespace(returncode=1, stdout="")
    return run


def test_owner_and_group_resolve_through_lark_cli(monkeypatch):
    monkeypatch.delenv("SERVEROS_NOTIFY_OWNER", raising=False)
    run = runner({"+get-user": json.dumps({"data": {"user": {"open_id": OWNER}}}),
                  "+chat-search": json.dumps({"data": {"items": [
                      {"name": "ServerOS 播报-旧", "chat_id": "oc_old000000000000000"},
                      {"name": "ServerOS 播报", "chat_id": "oc_" + "b" * 32}]}})})
    assert feishu.resolve("owner", run=run) == f"user:{OWNER}"
    assert feishu.resolve("chat-name:ServerOS 播报", run=run) == "chat:oc_" + "b" * 32
    assert feishu.resolve("chat:oc_x", run=run) == "chat:oc_x"


def test_an_unknown_group_is_refused_not_guessed():
    with pytest.raises(RuntimeError, match="no group by that exact name"):
        feishu.resolve("chat-name:不存在", run=runner({"+chat-search": json.dumps({"data": {"items": []}})}))


def test_health_reads_text_and_json():
    text = "bot      = ready\nuser     = needs_login\n"
    assert feishu.health(run=runner({"status": text}))["user"] == "needs_login"
    assert feishu.health(run=runner({"status": json.dumps({"bot": "ready", "user": {"status": "ready"}})}))["user"] == "ready"
    assert feishu.health(run=runner({}))["cli"] == "UNAVAILABLE"


def test_health_reads_the_identities_lark_cli_actually_prints():
    """`lark-cli auth status` nests both identities; reading the top level said
    UNKNOWN for a hub that was fully logged in."""
    out = json.dumps({"appId": "cli_x", "brand": "feishu", "identities": {
        "bot": {"status": "ready", "available": True},
        "user": {"status": "ready", "available": True, "openId": OWNER}}})
    state = feishu.health(run=runner({"status": out}))
    assert state == {"cli": "OK", "bot": "ready", "user": "ready"}


def test_buttons_start_the_cli_that_exists_not_just_the_first_name(monkeypatch):
    """The listener used to take the first candidate, the bare name, and report
    CLI_MISSING where the absolute path works."""
    tried = []
    seen = {}

    class FakeProc:
        pass

    def popen(argv, **kwargs):
        tried.append(argv[0])
        if argv[0] != "/opt/lark-cli":
            raise FileNotFoundError(argv[0])
        seen.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(feishu, "_cli", lambda: ["lark-cli", "/opt/lark-cli"])
    monkeypatch.setattr(feishu.subprocess, "Popen", popen)
    assert isinstance(feishu._spawn(feishu.CONSUME_CALLBACKS), FakeProc)
    assert tried == ["lark-cli", "/opt/lark-cli"]
    # `event consume` treats stdin EOF as "shut down"; a unit's stdin is
    # /dev/null, so the pipe we hold is what keeps the subscription alive.
    assert seen["stdin"] == feishu.subprocess.PIPE
    monkeypatch.setattr(feishu, "_cli", lambda: ["nowhere/lark-cli"])
    assert feishu._spawn(feishu.CONSUME_CALLBACKS) is None


def test_a_listener_that_never_gets_ready_says_why():
    listener = feishu.ButtonListener(OWNER, lambda value: "ok")
    listener.state = "STARTING"
    listener._drain(iter(["[event] consuming as cli_x\n",
                          "[event] stdin closed — shutting down. consume treats stdin EOF as exit signal\n"]))
    assert listener.state == "STARTING" and "stdin closed" in listener.last
    listener._drain(iter(["[event] ready event_key=card.action.trigger\n"]))
    assert listener.state == "LISTENING"


def test_a_click_counts_only_when_the_owner_made_it():
    value = {"fleet": "mute", "subject": "device:x|mem", "minutes": 60}
    click = {"event": {"operator_id": OWNER, "action_value": json.dumps(value)}}
    assert feishu.decide(click, OWNER) == value
    stranger = {"event": {"operator_id": "ou_" + "c" * 32, "action_value": json.dumps(value)}}
    assert feishu.decide(stranger, OWNER) is None
    unknown = {"event": {"operator_id": OWNER, "action_value": json.dumps({"fleet": "rm -rf"})}}
    assert feishu.decide(unknown, OWNER) is None


def test_buttons_queue_monitor_commands(tmp_path):
    control = MonitorControl(tmp_path)
    inbox = EventInbox(tmp_path / "inbox.sqlite")
    assert act_on_button({"fleet": "mute", "subject": "device:x|mem", "minutes": 60},
                         control, inbox) == "MUTE_QUEUED"
    assert act_on_button({"fleet": "pause_broadcast", "minutes": 120}, control, inbox) == "PAUSE_QUEUED"
    assert [c["verb"] for c in control.pending()] == ["mute", "pause_broadcast"]


def test_the_revert_button_undoes_the_last_tuning(tmp_path, monkeypatch):
    from fleet import intelligence
    path = tmp_path / "runtime-policy.yaml"
    path.write_text("tunable:\n  default_model: true\n")
    monkeypatch.setenv("SERVEROS_RUNTIME_POLICY", str(path))
    intelligence.set_default_model("device:tokyo-shared-dev-01", "claude-code", "x-model",
                                   by="t", reason="r")
    inbox = EventInbox(tmp_path / "inbox.sqlite")
    out = act_on_button({"fleet": "revert_tune", "device_ref": "device:tokyo-shared-dev-01",
                         "runtime": "claude-code"}, MonitorControl(tmp_path), inbox)
    assert out == "REVERTED"
    assert intelligence.default_choice("device:tokyo-shared-dev-01", "claude-code")["model"] is None


def test_open_alert_cards_carry_buttons_and_still_validate():
    card = cards.alert_card({"episode": "device:x|mem", "level": "ALERT", "subject": "内存",
                             "text": "95%", "first_seen": "2026-11-07T01:00:00Z",
                             "device_ref": "device:x"}, now=NOW)
    tags = json.dumps(card, ensure_ascii=False)
    assert '"tag": "button"' in tags and "1 小时内别再提醒" in tags
    assert "暂停播报" not in tags, "one button per card, not two"


def test_the_tuning_card_offers_revert():
    card = cards.tuning_card({"summary": "s", "evidence": "device:x|tune|claude-code"}, now=NOW)
    assert "撤销这次调整" in json.dumps(card, ensure_ascii=False)
