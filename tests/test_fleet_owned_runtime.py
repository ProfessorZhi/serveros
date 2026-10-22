"""The deployed owner entry must retain delivery evidence across reconstruction."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fleet_mcp.dispatch import DispatchGuard
from fleet_mcp.errors import FleetRefusal
from fleet_mcp.fleet import Principal
from fleet_mcp.identity import SessionKey
from test_fleet_dispatch_claims import owner_store
from test_fleet_create_session import Harness, TOKYO

ROOT = Path(__file__).resolve().parents[1]


def existing_store(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": "serveros/agent-control-store.v1",
                               "contexts": {"preserved": {"value": "unchanged"}}}))
    path.chmod(0o600)
    return owner_store(path)


def factory():
    name = "serveros_runtime_fleet_mcp_owner"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "runtime/fleet_mcp_owner.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name]


def test_owned_factory_uses_the_existing_daemon_store(tmp_path, monkeypatch):
    state = tmp_path / "state"
    path = state / "runtime/agent-control/store.json"
    existing_store(path)
    monkeypatch.setenv("SERVEROS_STATE_DIR", str(state))
    h = Harness(tmp_path)
    service = factory().build_service(h.config, providers=h.providers)
    principal = h.principal()
    first = service.create_session(principal, device=TOKYO, project="p-1", idempotency_key="x")
    assert first["idempotency_durability"] == "AGENT_CONTROL_STORE"
    calls = len(h.providers[TOKYO].created)
    rebuilt = factory().build_service(h.config, providers=h.providers)
    replay = rebuilt.create_session(principal, device=TOKYO, project="p-1", idempotency_key="x")
    assert replay["session_ref"] == first["session_ref"]
    assert len(h.providers[TOKYO].created) == calls
    assert json.loads(path.read_text())["contexts"]["preserved"] == {"value": "unchanged"}


def test_missing_owner_anchor_is_not_created(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVEROS_STATE_DIR", str(tmp_path / "absent-state"))
    h = Harness(tmp_path)
    with pytest.raises(FleetRefusal, match="existing ServerOS"):
        factory().build_service(h.config, providers=h.providers)
    assert not (tmp_path / "absent-state").exists()
    assert h.providers[TOKYO].created == []


def test_strict_owner_refuses_deleted_anchor_before_first_mutation(tmp_path):
    path = tmp_path / "store.json"
    existing_store(path)
    cls = type(owner_store(path))
    store = cls(path, require_existing=True)
    path.unlink()
    guard = DispatchGuard(store)
    with pytest.raises(FleetRefusal) as caught:
        guard.begin(Principal("owner", frozenset(), "pat"), "send", "target", "new",
                    device_ref=TOKYO, runtime="claude", params={"message": "task"})
    assert caught.value.code == "DISPATCH_STATE_UNAVAILABLE"
    assert not path.exists()


@pytest.mark.parametrize("contents", ["[]", "{}", '{"schema":"some-other-store"}'])
def test_owned_anchor_schema_is_checked(tmp_path, contents):
    path = tmp_path / "store.json"
    path.write_text(contents)
    path.chmod(0o600)
    cls = type(owner_store(tmp_path / "unused"))
    with pytest.raises(sys.modules["serveros_runtime_agent_control_store"].StoreError):
        cls(path, require_existing=True)
    assert path.read_text() == contents


def test_owned_anchor_cannot_be_a_symlink(tmp_path):
    target = tmp_path / "real.json"
    existing_store(target)
    path = tmp_path / "store.json"
    path.symlink_to(target)
    cls = type(owner_store(target))
    with pytest.raises(sys.modules["serveros_runtime_agent_control_store"].StoreError):
        cls(path, require_existing=True)


def test_strict_owner_does_not_read_a_disappearing_register_as_empty(tmp_path, monkeypatch):
    path = tmp_path / "store.json"
    cls = type(existing_store(path))
    store = cls(path, require_existing=True)
    original = store._existing_owner_document

    def removed_after_read():
        data = original()
        path.unlink()
        return data

    monkeypatch.setattr(store, "_existing_owner_document", removed_after_read)
    with pytest.raises(sys.modules["serveros_runtime_agent_control_store"].StoreError):
        store.get_dispatch_attempt("unknown")
    assert not path.exists()


def test_strict_owner_accepts_other_legitimate_atomic_writers(tmp_path):
    path = tmp_path / "store.json"
    cls = type(existing_store(path))
    observer = cls(path, require_existing=True)
    guard = DispatchGuard(cls(path, require_existing=True))
    principal = Principal("owner", frozenset(), "pat")
    key = SessionKey.compose(TOKYO, "claude", "owned-session")
    claim = guard.begin(principal, "send", key.ref, "task", device_ref=TOKYO,
                        runtime="claude", params={"message": "whole task"}, session_ref=key.ref)
    guard.complete(claim, {"session": key.as_dict(), "run_id": "real-run", "accepted": True})
    assert observer.get_dispatch_attempt(claim.key)["phase"] == "ACCEPTED"


def test_strict_owner_refuses_a_redirected_lock_sidecar(tmp_path):
    path = tmp_path / "store.json"
    cls = type(existing_store(path))
    other = tmp_path / "other-lock"
    other.write_text("")
    other.chmod(0o600)
    Path(str(path) + ".lock").symlink_to(other)
    with pytest.raises(sys.modules["serveros_runtime_agent_control_store"].StoreError):
        cls(path, require_existing=True)
    assert other.read_text() == ""


def test_strict_owner_rechecks_lock_before_mutation(tmp_path):
    path = tmp_path / "store.json"
    cls = type(existing_store(path))
    store = cls(path, require_existing=True)
    other = tmp_path / "other-lock"
    other.write_text("")
    other.chmod(0o600)
    Path(str(path) + ".lock").symlink_to(other)
    guard = DispatchGuard(store)
    with pytest.raises(FleetRefusal) as caught:
        guard.begin(Principal("owner", frozenset(), "pat"), "send", "target", "new",
                    device_ref=TOKYO, runtime="claude", params={"message": "task"})
    assert caught.value.code == "DISPATCH_STATE_UNAVAILABLE"
    assert json.loads(path.read_text()).get("dispatch_attempts", {}) == {}


def test_project_receipt_is_independent_and_survives_reconstruction(tmp_path):
    path = tmp_path / "store.json"
    store = existing_store(path)
    principal = Principal("owner", frozenset(), "pat")
    expected = str(tmp_path / "new-project")
    kwargs = dict(device_ref=TOKYO, runtime="claude", params={"name": "new-project"},
                  expected_project_path=expected)
    guard = DispatchGuard(store)
    claim = guard.begin(principal, "project.create", TOKYO, "project-key", **kwargs)
    receipt = {"project_id": "native-project-id", "project_path": expected, "accepted": True}
    guard.complete(claim, receipt)
    replay = DispatchGuard(owner_store(path)).begin(
        principal, "project.create", TOKYO, "project-key", **kwargs)
    assert replay.replay == receipt
    changed = {**kwargs, "params": {"name": "different-project"}}
    with pytest.raises(FleetRefusal) as caught:
        guard.begin(principal, "project.create", TOKYO, "project-key", **changed)
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    assert json.loads(path.read_text())["contexts"]["preserved"] == {"value": "unchanged"}


def test_project_claim_rejects_session_receipt(tmp_path):
    store = existing_store(tmp_path / "store.json")
    guard = DispatchGuard(store)
    principal = Principal("owner", frozenset(), "pat")
    claim = guard.begin(principal, "project.create", TOKYO, "project-key",
                        device_ref=TOKYO, runtime="claude", params={"name": "new"},
                        expected_project_path=str(tmp_path / "new"))
    ref = SessionKey.compose(TOKYO, "claude", "a-session").ref
    with pytest.raises(FleetRefusal) as caught:
        guard.complete(claim, {"session": {"session_ref": ref}, "accepted": True})
    assert caught.value.code == "DISPATCH_OUTCOME_UNKNOWN"


def test_accepted_receipt_replays_in_another_os_process(tmp_path):
    path = tmp_path / "store.json"
    existing_store(path)
    calls = tmp_path / "provider-calls"
    script = '''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tests.test_fleet_dispatch_claims import owner_store
from fleet_mcp.dispatch import DispatchGuard
from fleet_mcp.fleet import Principal
from fleet_mcp.identity import SessionKey
store = owner_store(Path(sys.argv[2]))
g = DispatchGuard(store)
p = Principal('owner', frozenset(), 'pat')
ref = SessionKey.compose('device:tokyo-shared-dev-01', 'claude', 'existing').ref
c = g.begin(p, 'send', ref, 'stable-task', device_ref='device:tokyo-shared-dev-01',
            runtime='claude', params={'message':'the whole long task'}, session_ref=ref)
if c.replay is None:
    with Path(sys.argv[3]).open('a') as f: f.write('one effect\\n')
    g.complete(c, {'session': {'session_ref':ref}, 'run_id':'real-run', 'accepted':True})
print(json.dumps({'replay':c.replay is not None, 'durability':g.durability}))
'''
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "tests"))
    results = []
    for _ in range(2):
        result = subprocess.run([sys.executable, "-c", script, str(ROOT), str(path), str(calls)],
                                capture_output=True, text=True, env=env, timeout=15)
        assert result.returncode == 0, result.stderr
        results.append(json.loads(result.stdout))
    assert results == [{"replay": False, "durability": "AGENT_CONTROL_STORE"},
                       {"replay": True, "durability": "AGENT_CONTROL_STORE"}]
    assert calls.read_text().splitlines() == ["one effect"]
