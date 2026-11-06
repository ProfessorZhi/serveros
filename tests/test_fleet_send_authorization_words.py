"""The control state says in words which grant lets a caller send, or why not.

Tokyo, 2026-11-06: `send_allowed: true` next to `session_allowlisted: false`
was read by a commander as "the owner must authorize again".
"""
from fleet_mcp.identity import SessionKey
from test_fleet_mcp_surface import Harness, TOKYO


def _state(h, key, principal):
    return h.service._session_control_state(principal, key)


def test_an_allowlisted_session_names_the_allowlist(tmp_path):
    key = SessionKey.compose(TOKYO, "claude", "s-1")
    h = Harness(tmp_path, send_allow=(key.ref,), allow_tokyo_send=True)
    state = _state(h, key, h.principal())
    assert state["send_allowed"] is True
    assert state["send_authorized_by"] == "SESSION_ALLOWLIST"
    assert state["send_denied_because"] is None


def test_a_missing_scope_says_so(tmp_path):
    key = SessionKey.compose(TOKYO, "claude", "s-1")
    h = Harness(tmp_path, send_allow=(key.ref,), allow_tokyo_send=True)
    state = _state(h, key, h.principal("fleet:read"))
    assert state["send_allowed"] is False
    assert "fleet:send" in state["send_denied_because"]


def test_a_device_without_send_says_so(tmp_path):
    key = SessionKey.compose(TOKYO, "claude", "s-1")
    h = Harness(tmp_path, send_allow=(key.ref,), allow_tokyo_send=False)
    state = _state(h, key, h.principal())
    assert state["send_allowed"] is False
    assert "not enabled for this device" in state["send_denied_because"]


def test_no_grant_and_no_allowlist_says_both(tmp_path):
    key = SessionKey.compose(TOKYO, "claude", "s-1")
    h = Harness(tmp_path, send_allow=(), allow_tokyo_send=True)
    state = _state(h, key, h.principal())
    assert state["send_allowed"] is False
    assert "operator grant" in state["send_denied_because"]
