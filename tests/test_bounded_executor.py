"""Scope refusals, replay and real offline container lifecycle (opt-in local image)."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("serveros_bounded_executor", ROOT / "runtime/bounded_executor.py")
executor = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = executor
spec.loader.exec_module(executor)
IMAGE = os.environ.get("SERVEROS_TEST_DOCKER_IMAGE", "sha256:" + "1" * 64)


@pytest.fixture
def setup(tmp_path):
    tree = tmp_path / "worktrees" / "demo" / "item"
    tree.mkdir(parents=True)
    receipts = tmp_path / "receipts"
    receipts.mkdir(mode=0o700)
    now = datetime.now(timezone.utc)
    envelope = {"action_key": "wake-test-one", "work_item_ref": "DEMO-1", "epoch": 1,
                "grant_ref": "grant-test", "issued_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=45)).isoformat(),
                "allowed_write_scope": [str(tree)]}
    bound = {"schema": executor.SCHEMA, "worktree": str(tree), "image": IMAGE,
             "memory_bytes": 64 * 1024**2, "cpus": 1, "pids_limit": 16}
    grant = {"engineering": bound, "actions": ["engineering.execute"], "items": ["DEMO-1"],
             "expires_at": envelope["expires_at"],
             "budget": {"wall_seconds": 45, "max_attempts": 2, "max_output_bytes": 10000}}
    record = {"write_scope": [str(tree)]}
    return envelope, grant, record, tree, receipts


def resolve(setup):
    env, grant, record, tree, _ = setup
    return executor.binding(envelope=env, grant=grant, checkpoint=record,
                            worktrees_root=tree.parents[1])


@pytest.mark.parametrize("mutation", ["missing_action", "mutable_image", "live_narrowed", "envelope_narrowed", "symlink", "shared_root"])
def test_scope_refuses_before_any_effect(setup, mutation):
    env, grant, record, tree, receipts = setup
    if mutation == "missing_action":
        grant["actions"] = []
    elif mutation == "mutable_image":
        grant["engineering"]["image"] = "python:latest"
    elif mutation == "live_narrowed":
        record["write_scope"] = [str(receipts)]
    elif mutation == "envelope_narrowed":
        env["allowed_write_scope"] = []
    elif mutation == "symlink":
        link = tree.parent / "alias"
        link.symlink_to(tree, target_is_directory=True)
        grant["engineering"]["worktree"] = str(link)
    else:
        grant["engineering"]["worktree"] = str(tree.parents[1])
    with pytest.raises(ValueError):
        resolve(setup)
    assert list(receipts.iterdir()) == []


def invoke(setup, argv, key="check"):
    env, grant, _, _, receipts = setup
    return executor.execute(envelope=env, grant=grant, resolved=resolve(setup),
                            key=key, argv=argv, receipts=receipts)


def test_bad_command_does_not_consume_attempt(setup):
    with pytest.raises(ValueError, match="argv"):
        invoke(setup, [])
    assert list(setup[-1].iterdir()) == []


@pytest.mark.skipif("SERVEROS_TEST_DOCKER_IMAGE" not in os.environ, reason="requires an explicitly selected existing local image")
def test_real_container_scope_limits_descendant_exit_and_replay(setup):
    outside = setup[-1].parent / "outside-sentinel"
    outside.write_text("must-not-be-mounted")
    command = '''import json, os, pathlib, socket, subprocess, sys
assert os.getuid() != 0
assert not pathlib.Path(%r).exists()
assert not pathlib.Path('/var/run/docker.sock').exists()
try:
    pathlib.Path('/etc/serveros-forbidden').write_text('x')
    raise AssertionError('root filesystem was writable')
except OSError: pass
s=socket.socket(); s.settimeout(.1)
try:
    s.connect(('1.1.1.1', 443))
    raise AssertionError('network was reachable')
except OSError: pass
limits={p:pathlib.Path('/sys/fs/cgroup/'+p).read_text().strip() for p in ('memory.max','pids.max','cpu.max')}
assert limits['memory.max'] == '67108864'
assert limits['pids.max'] == '16'
assert limits['cpu.max'] == '100000 100000'
pathlib.Path('/workspace/result.txt').write_text('one execution')
pathlib.Path('/artifacts/result.json').write_text(json.dumps(limits))
subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
print(json.dumps({'limits':limits,'scope':'verified'}))
''' % str(outside)
    result = invoke(setup, ["python3", "-I", "-c", command])
    assert result["receipt"]["process_exit_code"] == 0, result
    assert result["container"]["worker_stopped"] is True, result
    assert result["container"]["container_state"]["Running"] is False
    assert result["container"]["container_state"]["ExitCode"] == 0
    assert result["container"]["container_removed"] is True
    assert json.loads(result["output_tail"]["stdout"])["scope"] == "verified"
    assert (setup[3] / "result.txt").read_text() == "one execution"
    assert (Path(result["artifacts"]) / "result.json").is_file()
    again = invoke(setup, ["python3", "-I", "-c", command])
    assert again["receipt"]["replayed"] is True
    assert again["receipt"]["executed_now"] is False
    with pytest.raises(ValueError, match="different input"):
        invoke(setup, ["python3", "-c", "raise SystemExit(7)"])
    exists = subprocess.run([executor.DOCKER, "inspect", result["container"]["container_id"]], capture_output=True)
    assert exists.returncode != 0


@pytest.mark.skipif("SERVEROS_TEST_DOCKER_IMAGE" not in os.environ, reason="requires an explicitly selected existing local image")
def test_real_timeout_kills_container_and_does_not_replay(setup):
    setup[0]["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=3)).isoformat()
    result = invoke(setup, ["python3", "-c", "import time; time.sleep(60)"])
    assert result["container"]["worker_stopped"] is True, result
    assert result["container"]["container_state"]["ExitCode"] == 124
    assert result["container"]["container_removed"] is True
    assert invoke(setup, ["python3", "-c", "import time; time.sleep(60)"])["receipt"]["replayed"] is True


@pytest.mark.skipif("SERVEROS_TEST_DOCKER_IMAGE" not in os.environ, reason="requires an explicitly selected existing local image")
def test_real_output_limit_reconciles_container_before_return(setup):
    result = invoke(setup, ["python3", "-c", "import time; print('x'*20000, flush=True); time.sleep(60)"])
    assert result["receipt"]["status"] == "OUTPUT_LIMIT", result
    assert result["worker_stopped"] is True, result
    assert result["container"]["container_removed"] is True
